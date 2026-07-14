"""Tier-1 unit tests for ``MAFTravelOrchestrator``.

Focus on business logic that lives entirely in this file and does not depend
on the Foundry SDK being reachable: routing, fan-out, aggregation, prompt
shape, history formatting, voice-channel detection, and the ``ROUTE_TO_AGENT``
registry itself.

Every network call the orchestrator would normally make (`_run_foundry_agent`,
`_run_native_specialist`, hosted-agent dispatch) is stubbed via ``patch.object``
so the tests are fully offline and deterministic.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.handler.local_maf_orchestrator import (
    AgentDecision,
    AgentResult,
    FoundryAgentError,
    HOSTED_AGENTS,
    MAFTravelOrchestrator,
)


def _make() -> MAFTravelOrchestrator:
    """Build an orchestrator with the native SDK forced-ready so we skip
    the DefaultAzureCredential / FoundryAgent import path."""
    orch = MAFTravelOrchestrator({})
    orch._native_ready = True  # type: ignore[attr-defined]
    orch._native_init_error = ""  # type: ignore[attr-defined]
    return orch


# =========================================================================
# 1. Registry regression guards
# =========================================================================


class RouteRegistryTests(unittest.TestCase):
    """Guard rails on ``ROUTE_TO_AGENT`` -- adding/removing a route should be
    an explicit, reviewed change. These tests make silent drift impossible."""

    EXPECTED_ROUTES = {
        "FLIGHT_BOOKING": "FlightBookingAgent",
        "HOLIDAY_PACKAGE": "HolidayPackageAgent",
        "CRUISE": "CruiseDiscoveryAgent",
        "TOUR": "TourMatchingAgent",
        "INSPIRATION": "TravelInspirationAgent",
        "POST_BOOKING": "Post-BookingCocierge",
        "CONSULTANT": "ConsultantMatchAgent",
        "DEAL_ALERT": "DealAlertAgent",
        "GENERAL_FAQ": "GeneralFAQAgent",
        "USER_CONTEXT": "UserContextAgent",
        "TRIP_PLANNER": "TripPlannerAgent",
        # UC-01: VITA — Voice-Enabled On-Road Training Assistant
        "VITA": "VITAAgent",
    }

    def test_all_expected_routes_present(self):
        self.assertEqual(MAFTravelOrchestrator.ROUTE_TO_AGENT, self.EXPECTED_ROUTES)

    def test_trip_planner_route_maps_to_hosted_agent(self):
        agent = MAFTravelOrchestrator.ROUTE_TO_AGENT["TRIP_PLANNER"]
        self.assertIn(agent, HOSTED_AGENTS)

    def test_user_context_route_maps_to_hosted_agent(self):
        agent = MAFTravelOrchestrator.ROUTE_TO_AGENT["USER_CONTEXT"]
        self.assertIn(agent, HOSTED_AGENTS)

    def test_vita_route_maps_to_hosted_agent(self):
        agent = MAFTravelOrchestrator.ROUTE_TO_AGENT["VITA"]
        self.assertIn(agent, HOSTED_AGENTS)

    def test_all_agent_names_are_unique(self):
        agents = list(MAFTravelOrchestrator.ROUTE_TO_AGENT.values())
        self.assertEqual(len(agents), len(set(agents)))


# =========================================================================
# 2. Static helpers -- pure functions, no I/O
# =========================================================================


class IsVoiceChannelTests(unittest.TestCase):
    """Voice channels get a different prompt shape than text -- misclassifying
    the channel would ship a wall-of-text reply into a TTS pipeline (bad UX)
    or a compressed single-line reply into a rich web chat (poor detail)."""

    def test_matches_voice_prefixed_channels(self):
        for name in ("voice-web", "voice", "acs-voice"):
            self.assertTrue(
                MAFTravelOrchestrator._is_voice_channel({"channel": name}),
                f"expected voice for channel={name!r}",
            )

    def test_matches_phone_and_tel_and_acs(self):
        for name in ("phone", "tel", "acs-phone", "TELEPHONY", "Phone Web"):
            self.assertTrue(
                MAFTravelOrchestrator._is_voice_channel({"channel": name}),
                f"expected voice for channel={name!r}",
            )

    def test_text_channels_are_not_voice(self):
        for name in ("chat-web", "web", "teams", "email", "webchat", ""):
            self.assertFalse(
                MAFTravelOrchestrator._is_voice_channel({"channel": name}),
                f"expected text for channel={name!r}",
            )

    def test_missing_channel_key_is_not_voice(self):
        self.assertFalse(MAFTravelOrchestrator._is_voice_channel({}))

    def test_non_string_channel_is_coerced(self):
        # We use str() coercion; None becomes "None" which isn't voice-y.
        self.assertFalse(MAFTravelOrchestrator._is_voice_channel({"channel": None}))


class FormatConversationHistoryTests(unittest.TestCase):
    """History drives multi-turn continuity; a bug here would either leak the
    entire conversation into every prompt (token cost) or drop it entirely
    (agents forget context)."""

    def test_no_history_returns_sentinel(self):
        orch = _make()
        self.assertEqual(
            orch._format_conversation_history({}),
            "(No prior conversation)",
        )

    def test_empty_history_returns_sentinel(self):
        orch = _make()
        self.assertEqual(
            orch._format_conversation_history({"history": []}),
            "(No prior conversation)",
        )

    def test_non_list_history_returns_sentinel(self):
        orch = _make()
        self.assertEqual(
            orch._format_conversation_history({"history": "not a list"}),
            "(No prior conversation)",
        )

    def test_history_is_upper_cased_and_joined(self):
        orch = _make()
        result = orch._format_conversation_history({
            "history": [
                {"role": "user", "text": "Hi"},
                {"role": "assistant", "text": "Hello"},
            ]
        })
        self.assertEqual(result, "USER: Hi\nASSISTANT: Hello")

    def test_history_truncated_to_last_six_turns(self):
        orch = _make()
        turns = [{"role": "user", "text": f"turn {i}"} for i in range(10)]
        result = orch._format_conversation_history({"history": turns})
        # Only turns 4..9 should survive (last 6).
        self.assertNotIn("turn 3", result)
        self.assertIn("turn 4", result)
        self.assertIn("turn 9", result)

    def test_malformed_turn_is_skipped_not_crashed(self):
        orch = _make()
        result = orch._format_conversation_history({
            "history": [
                "just a string, not a dict",
                {"role": "user", "text": ""},        # empty text -> skipped
                {"role": "user"},                     # missing text -> skipped
                {"role": "assistant", "text": "OK"},
            ]
        })
        self.assertEqual(result, "ASSISTANT: OK")


# =========================================================================
# 3. Route parsing (`_route_with_orchestrator_agent`)
# =========================================================================


class SingleIntentRouteParsingTests(unittest.IsolatedAsyncioTestCase):
    async def _route(self, reply: str) -> str:
        orch = _make()
        with patch.object(orch, "_run_foundry_agent", new=AsyncMock(return_value=reply)):
            return await orch._route_with_orchestrator_agent("plan a flight", {})

    async def test_bare_token_reply_matches(self):
        self.assertEqual(await self._route("FLIGHT_BOOKING"), "FLIGHT_BOOKING")

    async def test_lowercase_reply_matches(self):
        self.assertEqual(await self._route("flight_booking"), "FLIGHT_BOOKING")

    async def test_reply_with_surrounding_whitespace(self):
        self.assertEqual(await self._route("   TRIP_PLANNER  \n"), "TRIP_PLANNER")

    async def test_token_embedded_in_prose_is_extracted(self):
        reply = "This is a FLIGHT_BOOKING request from a returning customer."
        self.assertEqual(await self._route(reply), "FLIGHT_BOOKING")

    async def test_unknown_reply_returns_empty_string(self):
        self.assertEqual(await self._route("HOTEL_BOOKING"), "")

    async def test_empty_reply_returns_empty_string(self):
        self.assertEqual(await self._route(""), "")

    async def test_first_matching_token_wins_when_multiple_present(self):
        # `_route_with_orchestrator_agent` walks tokens in ROUTE_TO_AGENT order;
        # FLIGHT_BOOKING is declared before CRUISE.
        self.assertEqual(await self._route("FLIGHT_BOOKING and CRUISE"), "FLIGHT_BOOKING")


# =========================================================================
# 4. `_orchestrator_agent` -- decision assembly + route_hint fallback
# =========================================================================


class OrchestratorAgentDecisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_route_returns_decision(self):
        orch = _make()
        with patch.object(
            orch, "_route_with_orchestrator_agent",
            new=AsyncMock(return_value="CRUISE"),
        ):
            decision = await orch._orchestrator_agent("book a cruise", {})
        self.assertEqual(decision.route, "CRUISE")
        self.assertAlmostEqual(decision.confidence, 0.9)
        self.assertIn("Foundry OrchestratorAgent", decision.rationale)

    async def test_empty_route_falls_back_to_route_hint(self):
        orch = _make()
        with patch.object(
            orch, "_route_with_orchestrator_agent",
            new=AsyncMock(return_value=""),
        ):
            decision = await orch._orchestrator_agent(
                "hello",
                context={"route_hint": "consultant"},
            )
        self.assertEqual(decision.route, "CONSULTANT")
        self.assertGreater(decision.confidence, 0.9)  # hint gets higher confidence
        self.assertIn("route_hint", decision.rationale)

    async def test_empty_route_and_no_hint_raises(self):
        orch = _make()
        with patch.object(
            orch, "_route_with_orchestrator_agent",
            new=AsyncMock(return_value=""),
        ):
            with self.assertRaises(FoundryAgentError):
                await orch._orchestrator_agent("ambiguous", context={})

    async def test_empty_route_and_bogus_hint_raises(self):
        orch = _make()
        with patch.object(
            orch, "_route_with_orchestrator_agent",
            new=AsyncMock(return_value=""),
        ):
            with self.assertRaises(FoundryAgentError):
                await orch._orchestrator_agent(
                    "ambiguous",
                    context={"route_hint": "MADE_UP_ROUTE"},
                )


# =========================================================================
# 5. `orchestrate` -- single-intent end-to-end (mocked)
# =========================================================================


class OrchestrateSingleIntentTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_message_returns_clarification_without_calling_agent(self):
        orch = _make()
        # If any inner method is invoked we'd know, so patch both to fail loudly.
        with patch.object(orch, "_orchestrator_agent", side_effect=AssertionError("should not be called")), \
             patch.object(orch, "_run_specialist_agent", side_effect=AssertionError("should not be called")):
            result = await orch.orchestrate("   ", context={})

        self.assertEqual(result["selected_agents"], [])
        self.assertEqual(result["specialist_outputs"], [])
        self.assertEqual(result["next_step"], "collect_intent")
        self.assertIn("Please tell me", result["spoken_reply"])
        self.assertIsNotNone(result["clarification_question"])

    async def test_happy_path_uses_agent_from_route(self):
        orch = _make()
        decision = AgentDecision(route="CRUISE", confidence=0.9, rationale="ok")
        specialist_out = AgentResult(agent="CruiseDiscoveryAgent", summary="Two cruises found.", confidence=0.8)

        with patch.object(orch, "_orchestrator_agent", new=AsyncMock(return_value=decision)) as router, \
             patch.object(orch, "_run_specialist_agent", new=AsyncMock(return_value=specialist_out)) as specialist:
            result = await orch.orchestrate("Show me Mediterranean cruises.", context={"channel": "web"})

        router.assert_awaited_once()
        specialist.assert_awaited_once()
        # Specialist name is looked up from ROUTE_TO_AGENT[decision.route].
        _, kwargs = specialist.call_args
        self.assertEqual(specialist.call_args.args[0], "CruiseDiscoveryAgent")
        self.assertEqual(result["selected_agents"], ["CruiseDiscoveryAgent"])
        self.assertEqual(result["spoken_reply"], "Two cruises found.")
        self.assertEqual(result["workflow_route"], "CRUISE")
        # workflow_trace has one router node + one specialist node.
        self.assertEqual(len(result["workflow_trace"]), 2)
        self.assertEqual(result["workflow_trace"][0]["node"], "OrchestratorAgent")
        self.assertEqual(result["workflow_trace"][1]["node"], "CruiseDiscoveryAgent")

    async def test_confidence_is_averaged(self):
        orch = _make()
        decision = AgentDecision(route="FLIGHT_BOOKING", confidence=1.0, rationale="ok")
        specialist_out = AgentResult(agent="FlightBookingAgent", summary="ok", confidence=0.6)

        with patch.object(orch, "_orchestrator_agent", new=AsyncMock(return_value=decision)), \
             patch.object(orch, "_run_specialist_agent", new=AsyncMock(return_value=specialist_out)):
            result = await orch.orchestrate("book a flight", context={})

        self.assertAlmostEqual(result["confidence"], 0.8, places=2)

    async def test_attachments_are_forwarded_to_specialist(self):
        orch = _make()
        decision = AgentDecision(route="TRIP_PLANNER", confidence=0.9, rationale="ok")
        specialist_out = AgentResult(agent="TripPlannerAgent", summary="Plan.", confidence=0.9)
        attachments = [{"filename": "boarding.pdf", "text": "TAP452 08:15"}]

        with patch.object(orch, "_orchestrator_agent", new=AsyncMock(return_value=decision)), \
             patch.object(orch, "_run_specialist_agent", new=AsyncMock(return_value=specialist_out)) as specialist:
            await orch.orchestrate("plan trip", context={}, attachments=attachments)

        self.assertIs(specialist.call_args.kwargs["attachments"], attachments)


# =========================================================================
# 6. `orchestrate_multi` -- fan-out + failure isolation + aggregation
# =========================================================================


class OrchestrateMultiIntentTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_message_returns_clarification(self):
        orch = _make()
        with patch.object(orch, "_detect_multi_routes", side_effect=AssertionError("should not be called")):
            result = await orch.orchestrate_multi("", context={})

        self.assertEqual(result["selected_agents"], [])
        self.assertEqual(result["orchestration_strategy"], "multi-intent")
        self.assertEqual(result["next_step"], "collect_intent")

    async def test_two_routes_fan_out_and_both_summaries_combined(self):
        orch = _make()
        specialist_by_agent = {
            "FlightBookingAgent": AgentResult(agent="FlightBookingAgent", summary="Flights: LHR->BCN.", confidence=0.9),
            "CruiseDiscoveryAgent": AgentResult(agent="CruiseDiscoveryAgent", summary="Cruise: 7 nights.", confidence=0.7),
        }

        async def _fake_specialist(agent_name, *, message, context, attachments=None):
            return specialist_by_agent[agent_name]

        with patch.object(
            orch, "_detect_multi_routes",
            new=AsyncMock(return_value=["FLIGHT_BOOKING", "CRUISE"]),
        ), patch.object(orch, "_run_specialist_agent", side_effect=_fake_specialist) as specialist:
            result = await orch.orchestrate_multi("book a flight and a cruise", context={"channel": "web"})

        self.assertEqual(specialist.await_count, 2)
        self.assertEqual(result["selected_agents"], ["FlightBookingAgent", "CruiseDiscoveryAgent"])
        # Both summaries appear in the combined reply, prefixed with the lead phrase.
        self.assertIn("Here's how I can help across", result["spoken_reply"])
        self.assertIn("Flights: LHR->BCN.", result["spoken_reply"])
        self.assertIn("Cruise: 7 nights.", result["spoken_reply"])
        self.assertEqual(result["workflow_route"], "FLIGHT_BOOKING+CRUISE")
        self.assertEqual(result["orchestration_strategy"], "multi-intent")
        # Aggregate confidence is the mean of successful specialists.
        self.assertAlmostEqual(result["confidence"], 0.8, places=2)

    async def test_one_failing_specialist_does_not_kill_the_turn(self):
        orch = _make()

        async def _fake_specialist(agent_name, *, message, context, attachments=None):
            if agent_name == "FlightBookingAgent":
                raise RuntimeError("flight backend down")
            return AgentResult(agent=agent_name, summary="Cruise ok.", confidence=0.8)

        with patch.object(
            orch, "_detect_multi_routes",
            new=AsyncMock(return_value=["FLIGHT_BOOKING", "CRUISE"]),
        ), patch.object(orch, "_run_specialist_agent", side_effect=_fake_specialist):
            result = await orch.orchestrate_multi("book both", context={})

        # Both agents appear in the output list; the failing one carries an error.
        outputs_by_agent = {o["agent"]: o for o in result["specialist_outputs"]}
        self.assertIn("flight backend down", outputs_by_agent["FlightBookingAgent"].get("error", ""))
        self.assertEqual(outputs_by_agent["CruiseDiscoveryAgent"]["summary"], "Cruise ok.")
        # Failed specialist does NOT contribute to aggregate confidence.
        self.assertAlmostEqual(result["confidence"], 0.8, places=2)
        # Combined reply only contains the successful summary.
        self.assertEqual(result["spoken_reply"], "Cruise ok.")

    async def test_all_specialists_fail_returns_fallback_reply(self):
        orch = _make()

        async def _fake_specialist(agent_name, *, message, context, attachments=None):
            raise RuntimeError(f"{agent_name} unreachable")

        with patch.object(
            orch, "_detect_multi_routes",
            new=AsyncMock(return_value=["FLIGHT_BOOKING", "CRUISE"]),
        ), patch.object(orch, "_run_specialist_agent", side_effect=_fake_specialist):
            result = await orch.orchestrate_multi("book both", context={})

        # No valid summaries -> fallback line; confidence defaults to 0.5.
        self.assertEqual(result["spoken_reply"], "I can help with travel planning. Tell me what you need.")
        self.assertAlmostEqual(result["confidence"], 0.5)
        self.assertEqual(len(result["specialist_outputs"]), 2)
        for out in result["specialist_outputs"]:
            self.assertIn("error", out)

    async def test_no_valid_routes_falls_back_to_consultant(self):
        orch = _make()
        specialist_out = AgentResult(agent="ConsultantMatchAgent", summary="Let me connect you.", confidence=0.7)

        with patch.object(
            orch, "_detect_multi_routes",
            new=AsyncMock(return_value=[]),
        ), patch.object(orch, "_run_specialist_agent", new=AsyncMock(return_value=specialist_out)) as specialist:
            result = await orch.orchestrate_multi("something vague", context={})

        # Empty routes list -> defaults to ConsultantMatchAgent (line 258).
        specialist.assert_awaited_once()
        self.assertEqual(specialist.call_args.args[0], "ConsultantMatchAgent")
        self.assertEqual(result["selected_agents"], ["ConsultantMatchAgent"])

    async def test_duplicate_routes_are_deduped(self):
        orch = _make()
        specialist_out = AgentResult(agent="FlightBookingAgent", summary="ok", confidence=0.9)

        with patch.object(
            orch, "_detect_multi_routes",
            new=AsyncMock(return_value=["FLIGHT_BOOKING", "FLIGHT_BOOKING"]),
        ), patch.object(orch, "_run_specialist_agent", new=AsyncMock(return_value=specialist_out)) as specialist:
            result = await orch.orchestrate_multi("flights please", context={})

        self.assertEqual(specialist.await_count, 1)
        self.assertEqual(result["selected_agents"], ["FlightBookingAgent"])


# =========================================================================
# 7. `_route_with_multi_intent_orchestrator` -- parsing + cap-to-3
# =========================================================================


class MultiIntentRouteParsingTests(unittest.IsolatedAsyncioTestCase):
    async def _routes(self, reply: str) -> list[str]:
        orch = _make()
        with patch.object(orch, "_run_foundry_agent", new=AsyncMock(return_value=reply)):
            return await orch._route_with_multi_intent_orchestrator("msg", {})

    async def test_single_token_reply(self):
        self.assertEqual(await self._routes("FLIGHT_BOOKING"), ["FLIGHT_BOOKING"])

    async def test_comma_separated_reply_preserves_iteration_order(self):
        # The parser walks ROUTE_TO_AGENT keys in declaration order, not reply
        # order -- documenting the current behaviour as a regression guard.
        result = await self._routes("CRUISE, FLIGHT_BOOKING, TOUR")
        self.assertEqual(set(result), {"FLIGHT_BOOKING", "CRUISE", "TOUR"})

    async def test_no_tokens_returns_empty(self):
        self.assertEqual(await self._routes("no idea what you want"), [])

    async def test_capped_at_three_routes(self):
        # Reply mentions 5 valid tokens; parser must cap at 3.
        reply = "FLIGHT_BOOKING, CRUISE, TOUR, HOLIDAY_PACKAGE, POST_BOOKING"
        result = await self._routes(reply)
        self.assertEqual(len(result), 3)

    async def test_duplicate_tokens_in_reply_are_deduped(self):
        result = await self._routes("CRUISE and CRUISE and CRUISE")
        self.assertEqual(result, ["CRUISE"])

    async def test_case_insensitive_parsing(self):
        # The parser upper-cases the reply before scanning.
        self.assertEqual(await self._routes("flight_booking"), ["FLIGHT_BOOKING"])


class DetectMultiRoutesTests(unittest.IsolatedAsyncioTestCase):
    """Falls through to ``context.route_hint`` when parsing produces nothing."""

    async def test_valid_routes_used_directly(self):
        orch = _make()
        with patch.object(
            orch, "_route_with_multi_intent_orchestrator",
            new=AsyncMock(return_value=["CRUISE"]),
        ):
            self.assertEqual(await orch._detect_multi_routes("m", {}), ["CRUISE"])

    async def test_empty_routes_falls_back_to_route_hint(self):
        orch = _make()
        with patch.object(
            orch, "_route_with_multi_intent_orchestrator",
            new=AsyncMock(return_value=[]),
        ):
            self.assertEqual(
                await orch._detect_multi_routes("m", {"route_hint": "cruise"}),
                ["CRUISE"],
            )

    async def test_no_routes_and_no_hint_raises(self):
        orch = _make()
        with patch.object(
            orch, "_route_with_multi_intent_orchestrator",
            new=AsyncMock(return_value=[]),
        ):
            with self.assertRaises(FoundryAgentError):
                await orch._detect_multi_routes("m", {})


# =========================================================================
# 8. `_combine_specialist_summaries`
# =========================================================================


class CombineSpecialistSummariesTests(unittest.TestCase):
    def test_no_valid_returns_generic_fallback(self):
        orch = _make()
        combined = orch._combine_specialist_summaries([
            AgentResult(agent="A", summary="", confidence=0.0, error="boom"),
        ])
        self.assertEqual(combined, "I can help with travel planning. Tell me what you need.")

    def test_single_valid_returns_summary_unchanged(self):
        orch = _make()
        combined = orch._combine_specialist_summaries([
            AgentResult(agent="A", summary="just this.", confidence=0.9),
            AgentResult(agent="B", summary="", confidence=0.0, error="down"),
        ])
        self.assertEqual(combined, "just this.")

    def test_multiple_valid_are_lead_prefixed_and_space_joined(self):
        orch = _make()
        combined = orch._combine_specialist_summaries([
            AgentResult(agent="A", summary="One.", confidence=0.9),
            AgentResult(agent="B", summary="Two.", confidence=0.7),
        ])
        self.assertTrue(combined.startswith("Here's how I can help across"))
        self.assertIn("One.", combined)
        self.assertIn("Two.", combined)


# =========================================================================
# 9. `_run_specialist_agent` -- prompt shape by channel
# =========================================================================


class SpecialistPromptShapeTests(unittest.IsolatedAsyncioTestCase):
    """The prompt sent to the specialist differs by channel and history state.
    Getting this wrong ships either verbose walls-of-text into TTS or bare
    single-line prompts into rich chat surfaces."""

    async def _capture_prompt(self, *, channel: str, history=None) -> str:
        orch = _make()
        recorded: dict[str, str] = {}

        async def _fake_native(name, prompt):
            recorded["prompt"] = prompt
            return "reply"

        with patch.object(orch, "_run_native_specialist", side_effect=_fake_native):
            await orch._run_specialist_agent(
                "FlightBookingAgent",
                message="book LHR to LAX",
                context={"channel": channel, "history": history or []},
            )
        return recorded["prompt"]

    async def test_web_channel_first_turn_sends_bare_message(self):
        prompt = await self._capture_prompt(channel="chat-web")
        self.assertEqual(prompt, "book LHR to LAX")

    async def test_web_channel_with_history_wraps_in_context_block(self):
        prompt = await self._capture_prompt(
            channel="chat-web",
            history=[{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hey"}],
        )
        self.assertIn("Conversation history:", prompt)
        self.assertIn("USER: hi", prompt)
        self.assertIn("Current user request: book LHR to LAX", prompt)

    async def test_voice_channel_always_wraps_in_context_block(self):
        # No history but voice channel -- still gets the history block (with
        # "(No prior conversation)") so TTS gets consistent structure.
        prompt = await self._capture_prompt(channel="voice-web")
        self.assertIn("Conversation history:", prompt)
        self.assertIn("(No prior conversation)", prompt)
        self.assertIn("Current user request: book LHR to LAX", prompt)

    async def test_identity_context_is_prepended_when_present(self):
        orch = _make()
        recorded: dict[str, str] = {}

        async def _fake_native(name, prompt):
            recorded["prompt"] = prompt
            return "reply"

        with patch.object(orch, "_run_native_specialist", side_effect=_fake_native):
            await orch._run_specialist_agent(
                "TripPlannerAgent",
                message="who initiated this request?",
                context={
                    "channel": "chat-web",
                    "requester_identity": {
                        "display_name": "Ada Lovelace",
                        "email": "ada@example.com",
                        "user_id": "user-123",
                        "identity_provider": "aad",
                    },
                    "agent_identity": {
                        "agent_name": "TripPlannerAgent",
                        "entra_client_id": "agent-client-id",
                        "teams_bot_app_id": "bot-app-id",
                    },
                },
            )

        prompt = recorded["prompt"]
        self.assertEqual(prompt, "who initiated this request?")
        self.assertNotIn("[REQUEST IDENTITY]", prompt)
        self.assertNotIn("Ada Lovelace", prompt)
        self.assertNotIn("ada@example.com", prompt)
        self.assertNotIn("user-123", prompt)
        self.assertNotIn("agent-client-id", prompt)
        self.assertNotIn("bot-app-id", prompt)

    async def test_identity_context_log_is_sanitized(self):
        orch = _make()

        async def _fake_native(name, prompt):
            return "reply"

        with patch.object(orch, "_run_native_specialist", side_effect=_fake_native):
            with self.assertLogs("app.handler.local_maf_orchestrator", level="INFO") as logs:
                await orch._run_specialist_agent(
                    "TripPlannerAgent",
                    message="who initiated this request?",
                    context={
                        "channel": "teams",
                        "conversation_id": "teams-conversation-123",
                        "requester_identity": {
                            "authenticated": True,
                            "display_name": "Ada Lovelace",
                            "email": "ada@example.com",
                            "user_id": "aad-object-id-1234567890",
                            "identity_provider": "teams",
                        },
                        "agent_identity": {
                            "agent_name": "TripPlannerAgent",
                            "entra_client_id": "11111111-2222-3333-4444-agent9999",
                            "teams_bot_app_id": "aaaaaaaa-bbbb-cccc-dddd-bot8888",
                        },
                    },
                )

        output = "\n".join(logs.output)
        self.assertIn("travel_identity_context", output)
        self.assertIn("channel=teams", output)
        self.assertIn("authenticated=true", output)
        self.assertIn("identity_provider=teams", output)
        self.assertIn("agent_name=TripPlannerAgent", output)
        self.assertIn("agent_entra_client_id_set=true", output)
        self.assertIn("agent_entra_client_id_suffix=gent9999", output)
        self.assertIn("teams_bot_app_id_suffix=-bot8888", output)
        self.assertNotIn("Ada Lovelace", output)
        self.assertNotIn("ada@example.com", output)
        self.assertNotIn("aad-object-id-1234567890", output)
        self.assertNotIn("teams-conversation-123", output)

    async def test_user_context_agent_prompt_minimizes_requester_identity(self):
        orch = _make()
        recorded: dict[str, str] = {}

        async def _fake_native(name, prompt):
            recorded["prompt"] = prompt
            return "reply"

        with patch.object(orch, "_run_native_specialist", side_effect=_fake_native):
            await orch._run_specialist_agent(
                "UserContextAgent",
                message=(
                    "Who initiated this request? My backup phone is +1 (425) 555-0100 "
                    "and I live at 123 Market Street."
                ),
                context={
                    "channel": "teams",
                    "requester_identity": {
                        "authenticated": True,
                        "display_name": "Ada Lovelace",
                        "email": "ada@example.com",
                        "user_id": "aad-object-id-1234567890",
                        "identity_provider": "teams",
                    },
                    "agent_identity": {
                        "agent_name": "TripPlannerAgent",
                        "entra_client_id": "11111111-2222-3333-4444-agent9999",
                        "teams_bot_app_id": "aaaaaaaa-bbbb-cccc-dddd-bot8888",
                    },
                },
            )

        prompt = recorded["prompt"]
        self.assertNotIn("[REQUEST IDENTITY]", prompt)
        self.assertNotIn("Initiating user authenticated", prompt)
        self.assertNotIn("Identity provider: teams", prompt)
        self.assertNotIn("Requester personal fields", prompt)
        self.assertNotIn("diagnostic-only infrastructure identifiers", prompt)
        self.assertNotIn("Agent Entra client ID", prompt)
        self.assertNotIn("Teams bot Entra app ID", prompt)
        self.assertNotIn("Ada Lovelace", prompt)
        self.assertNotIn("ada@example.com", prompt)
        self.assertNotIn("aad-object-id-1234567890", prompt)
        self.assertIn("+1 (425) 555-0100", prompt)
        self.assertIn("123 Market Street", prompt)


if __name__ == "__main__":
    unittest.main()
