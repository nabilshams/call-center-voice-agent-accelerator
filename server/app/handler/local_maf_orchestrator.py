"""Local Microsoft Agent Framework travel orchestrator.

Binds to the prompt agents that are defined in Azure AI Foundry. Specialist
agents (e.g. ``FlightBookingAgent``) are bound by name to their latest published
version via ``FoundryAgent``, so their instructions, tools, and behavior are
authored once in Foundry and observed here automatically. Routing is delegated to the Foundry
``OrchestratorAgent`` (and ``Multi-IntentOrchestrator`` for multi-intent requests).
Each specialist agent owns its own slot-filling and follow-up questions. Every
agent—router or specialist—must be defined in Foundry; there is no code-defined
fallback agent.

There is no deterministic fallback: if the Foundry SDK/config is unavailable, or
a Foundry agent cannot be reached or run, the orchestrator raises
``FoundryAgentError`` instead of degrading to canned replies.
"""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class FoundryAgentError(RuntimeError):
    """Raised when the Azure AI Foundry SDK/config is unavailable or an agent
    cannot be reached or run.

    Surfaced loudly so that Foundry misconfiguration—wrong endpoint, missing role
    assignment, auth failure, or an unavailable SDK—is obvious instead of hidden
    behind a generic canned reply.
    """


# Only agents in this set will receive user-uploaded attachments in their
# prompt. Everyone else gets a warning log and the attachments are ignored --
# routing something like "here's my booking PDF, book me a flight" to
# FlightBookingAgent should not silently inject file text into a slot-fill
# agent that has no instructions for it.
#
# When adding a new agent here, its Foundry instructions MUST also be updated
# to describe how to interpret the [ATTACHMENT: filename] ... [END ATTACHMENT]
# blocks (see TripPlannerAgent/main.py and PostBookingCocierge/agent.yaml for
# the reference wording, including the injection guard and identity carve-out).
AGENTS_ACCEPTING_ATTACHMENTS: set[str] = {
    "TripPlannerAgent",
    "Post-BookingCocierge",
}

# Foundry hosted agents (deployed via `azd deploy <AgentName>`) cannot be
# invoked through the standard `/openai/v1/responses` endpoint that
# ``FoundryAgent`` targets. They must go through the hosted-agent endpoint
# ``/agents/<name>/endpoint/protocols/openai/responses``. Names in this set
# are dispatched through ``_run_hosted_agent`` instead of
# ``_run_foundry_agent``.
HOSTED_AGENTS: set[str] = {"TripPlannerAgent"}


@dataclass(frozen=True)
class AgentDecision:
    route: str
    confidence: float
    rationale: str


@dataclass(frozen=True)
class AgentResult:
    agent: str
    summary: str
    confidence: float
    error: str = ""


class MAFTravelOrchestrator:
    """In-process orchestrator that runs every travel request through the agents
    defined in Azure AI Foundry (no deterministic fallback)."""

    ROUTE_TO_AGENT = {
        "FLIGHT_BOOKING": "FlightBookingAgent",
        "HOLIDAY_PACKAGE": "HolidayPackageAgent",
        "CRUISE": "CruiseDiscoveryAgent",
        "TOUR": "TourMatchingAgent",
        "INSPIRATION": "TravelInspirationAgent",
        "POST_BOOKING": "Post-BookingCocierge",
        "CONSULTANT": "ConsultantMatchAgent",
        "DEAL_ALERT": "DealAlertAgent",
        "GENERAL_FAQ": "GeneralFAQAgent",
        # Hosted Foundry agent -- deployed via `azd deploy TripPlannerAgent`.
        # See src/travel_agency/TripPlannerAgent/{agent.yaml,main.py}.
        "TRIP_PLANNER": "TripPlannerAgent",
    }

    def __init__(self, config: dict[str, Any]):
        self._config = config
        self._native_enabled = str(config.get("MAF_NATIVE_SDK_ENABLED", "true")).lower() == "true"
        self._project_endpoint = (
            config.get("MAF_PROJECT_ENDPOINT")
            or config.get("FOUNDRY_PROJECT_ENDPOINT")
            or ""
        )
        self._managed_identity_client_id = (
            config.get("AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID")
            or config.get("AZURE_CLIENT_ID")
            or ""
        )

        self._foundry_agent_cls: Any = None
        self._async_credential: Any = None
        self._agent_cache: dict[str, Any] = {}          # name -> FoundryAgent
        # Hosted-agent plumbing: separate AIProjectClient with
        # ``allow_preview=True`` (required for hosted-agent OpenAI clients) and
        # a per-agent AsyncOpenAI cache pointed at each hosted agent's endpoint.
        self._hosted_project_client: Any = None
        self._hosted_openai_clients: dict[str, Any] = {}
        self._hosted_model = (
            config.get("MAF_MODEL")
            or config.get("AZURE_OPENAI_CHAT_DEPLOYMENT")
            or ""
        )
        self._native_ready = False
        self._native_init_error = ""

        self._initialize_native_sdk()

    @property
    def _specialist_agent_names(self) -> set[str]:
        """Names of the specialist agents expected to be defined in Foundry."""
        return set(self.ROUTE_TO_AGENT.values())

    def _initialize_native_sdk(self):
        if not self._native_enabled:
            self._native_init_error = "MAF native SDK disabled by configuration"
            return

        if not self._project_endpoint:
            self._native_init_error = "Missing MAF project endpoint"
            return

        try:
            from agent_framework.foundry import FoundryAgent  # type: ignore
            from azure.identity.aio import DefaultAzureCredential  # type: ignore

            self._foundry_agent_cls = FoundryAgent
            self._async_credential = DefaultAzureCredential(
                managed_identity_client_id=self._managed_identity_client_id or None
            )
            self._native_ready = True
        except (ImportError, RuntimeError, ValueError, TypeError, AttributeError) as exc:
            self._native_init_error = str(exc)
            logger.warning("Native MAF SDK initialization failed: %s", exc)

    def _require_native(self) -> None:
        """Fail loudly when the Azure AI Foundry SDK could not be initialized.

        There is no deterministic fallback: every request must run through the
        agents defined in Azure AI Foundry, so a failed/incomplete SDK init is a
        hard error instead of a silent degradation.
        """
        if not self._native_ready:
            raise FoundryAgentError(
                f"Azure AI Foundry agents are unavailable: {self._native_init_error}"
            )

    async def orchestrate(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        *,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not message or not message.strip():
            return {
                "spoken_reply": "Please tell me what you want help with for your trip.",
                "clarification_question": "Would you like help with flights, holiday packages, cruises, tours, or post-booking support?",
                "selected_agents": [],
                "specialist_outputs": [],
                "confidence": 0.2,
                "next_step": "collect_intent",
            }

        ctx = context or {}

        self._require_native()
        decision = await self._orchestrator_agent(message=message, context=ctx)
        selected_agent = self.ROUTE_TO_AGENT.get(decision.route, "ConsultantMatchAgent")
        specialist_result = await self._run_specialist_agent(
            selected_agent, message=message, context=ctx, attachments=attachments,
        )

        workflow_trace = [
            {
                "node": "OrchestratorAgent",
                "route": decision.route,
                "confidence": decision.confidence,
                "rationale": decision.rationale,
            },
            {
                "node": selected_agent,
                "confidence": specialist_result.confidence,
            },
        ]

        return {
            "spoken_reply": specialist_result.summary,
            "clarification_question": None,
            "selected_agents": [selected_agent],
            "specialist_outputs": [
                {
                    "agent": specialist_result.agent,
                    "request": message,
                    "summary": specialist_result.summary,
                    "confidence": specialist_result.confidence,
                }
            ],
            "confidence": round((decision.confidence + specialist_result.confidence) / 2, 2),
            "next_step": "present_options",
            "workflow_route": decision.route,
            "workflow_trace": workflow_trace,
        }

    async def orchestrate_multi(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        *,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Multi-Intent orchestration.

        Detects one or more intents in a single request, fans the work out to the
        matching specialist agents in parallel, waits for every response, then
        aggregates them into one combined reply.
        """
        if not message or not message.strip():
            return {
                "spoken_reply": "Please tell me what you want help with for your trip.",
                "clarification_question": "Would you like help with flights, holiday packages, cruises, tours, or post-booking support?",
                "selected_agents": [],
                "specialist_outputs": [],
                "confidence": 0.2,
                "next_step": "collect_intent",
                "orchestration_strategy": "multi-intent",
            }

        ctx = context or {}

        self._require_native()
        routes = await self._detect_multi_routes(message=message, context=ctx)
        selected_agents: list[str] = []
        for route in routes:
            agent = self.ROUTE_TO_AGENT.get(route)
            if agent and agent not in selected_agents:
                selected_agents.append(agent)
        if not selected_agents:
            selected_agents = ["ConsultantMatchAgent"]

        # Fan out to every selected specialist concurrently and wait for all of
        # them. return_exceptions keeps one failing specialist from taking down the
        # whole turn: failures are captured per-agent and surfaced in the trace
        # instead of disappearing or raising.
        # Attachments (if any) are only forwarded to agents in
        # AGENTS_ACCEPTING_ATTACHMENTS -- _run_specialist_agent enforces this,
        # so passing the same list to every fan-out call is safe.
        if attachments and not any(
            a in AGENTS_ACCEPTING_ATTACHMENTS for a in selected_agents
        ):
            logger.warning(
                "attachments_ignored_multi selected_agents=%s attachment_count=%s "
                "(no attachment-accepting specialist in the fan-out)",
                selected_agents,
                len(attachments),
            )
        raw_results = await asyncio.gather(
            *[
                self._run_specialist_agent(
                    agent, message=message, context=ctx, attachments=attachments,
                )
                for agent in selected_agents
            ],
            return_exceptions=True,
        )
        results: list[AgentResult] = []
        for agent, outcome in zip(selected_agents, raw_results):
            if isinstance(outcome, Exception):
                logger.warning(
                    "Specialist agent '%s' failed during multi-intent fan-out: %s",
                    agent,
                    outcome,
                )
                results.append(
                    AgentResult(agent=agent, summary="", confidence=0.0, error=str(outcome))
                )
            else:
                results.append(outcome)
        logger.info(
            "orchestrate_multi routes=%s selected_agents=%s executed=%s",
            routes,
            selected_agents,
            [(r.agent, bool(r.summary), bool(r.error)) for r in results],
        )

        specialist_outputs: list[dict[str, Any]] = []
        confidences: list[float] = []
        workflow_trace: list[dict[str, Any]] = [
            {
                "node": "Multi-IntentOrchestrator",
                "routes": routes,
                "parallel_agents": len(selected_agents),
            }
        ]

        for result in results:
            output = {
                "agent": result.agent,
                "request": message,
                "summary": result.summary,
                "confidence": result.confidence,
            }
            node = {"node": result.agent, "confidence": result.confidence}
            if result.error:
                output["error"] = result.error
                node["error"] = result.error
            specialist_outputs.append(output)
            workflow_trace.append(node)
            # Only successful specialists contribute to the aggregate confidence so
            # a failed agent does not drag the overall score to zero.
            if not result.error:
                confidences.append(result.confidence)

        combined_reply = self._combine_specialist_summaries(results)
        avg_confidence = round(sum(confidences) / len(confidences), 2) if confidences else 0.5

        return {
            "spoken_reply": combined_reply,
            "clarification_question": None,
            "selected_agents": selected_agents,
            "specialist_outputs": specialist_outputs,
            "confidence": avg_confidence,
            "next_step": "present_options",
            "workflow_route": "+".join(routes),
            "workflow_trace": workflow_trace,
            "orchestration_strategy": "multi-intent",
        }

    async def _detect_multi_routes(self, message: str, context: dict[str, Any]) -> list[str]:
        """Detect one or more intent routes for parallel orchestration."""
        routes = await self._route_with_multi_intent_orchestrator(message, context)
        if routes:
            return routes

        if isinstance(context.get("route_hint"), str):
            hint = context["route_hint"].strip().upper()
            if hint in self.ROUTE_TO_AGENT:
                return [hint]

        raise FoundryAgentError(
            "Multi-IntentOrchestrator did not return any valid routes and no route_hint was provided."
        )

    async def _route_with_multi_intent_orchestrator(self, message: str, context: dict[str, Any]) -> list[str]:
        """Ask the Foundry-defined Multi-IntentOrchestrator for every applicable route.

        Routing logic and instructions live in Azure AI Foundry; this forwards the
        request and parses the returned tokens. The agent must exist in Foundry —
        there is no local routing fallback.
        """
        prompt = (
            "Identify all travel intents in this request.\n"
            f"Message: {message}\n"
            f"Context: {context}\n"
            "Valid tokens: FLIGHT_BOOKING, HOLIDAY_PACKAGE, CRUISE, TOUR, INSPIRATION, "
            "POST_BOOKING, CONSULTANT, DEAL_ALERT, TRIP_PLANNER.\n"
            "Answer with comma-separated tokens only, most relevant first."
        )
        reply = await self._run_foundry_agent("Multi-IntentOrchestrator", prompt)
        upper = reply.strip().upper()
        found: list[str] = []
        for token in self.ROUTE_TO_AGENT:
            if re.search(rf"\b{token}\b", upper) and token not in found:
                found.append(token)
        logger.info(
            "Multi-IntentOrchestrator raw=%r parsed_routes=%s returned=%s",
            reply,
            found,
            found[:3],
        )
        return found[:3]

    def _combine_specialist_summaries(self, results: list[AgentResult]) -> str:
        """Merge multiple specialist summaries into one cohesive voice-safe reply."""
        valid = [result for result in results if result.summary]
        if not valid:
            return "I can help with travel planning. Tell me what you need."
        if len(valid) == 1:
            return valid[0].summary

        lead = "Here's how I can help across the areas you mentioned. "
        body = " ".join(result.summary for result in valid)
        return lead + body

    async def _orchestrator_agent(self, message: str, context: dict[str, Any]) -> AgentDecision:
        route = await self._route_with_orchestrator_agent(message, context)
        if route in self.ROUTE_TO_AGENT:
            return AgentDecision(
                route=route,
                confidence=0.9,
                rationale="Route selected by Foundry OrchestratorAgent.",
            )

        # Explicit caller override when the model's answer was unparseable.
        if isinstance(context.get("route_hint"), str):
            hint = context["route_hint"].strip().upper()
            if hint in self.ROUTE_TO_AGENT:
                return AgentDecision(
                    route=hint,
                    confidence=0.97,
                    rationale="Route selected from explicit context.route_hint.",
                )

        raise FoundryAgentError(
            "OrchestratorAgent did not return a valid route and no route_hint was provided."
        )

    @staticmethod
    def _is_voice_channel(context: dict[str, Any]) -> bool:
        """Return True when the request originated from a voice/telephony channel.

        Voice channels (e.g. ``voice-web``, ACS phone calls) need short,
        TTS-friendly replies. Text channels (e.g. ``chat-web``) can render the
        agent's full, detailed answer exactly as authored in Foundry.
        """
        channel = str(context.get("channel", "")).lower()
        return any(token in channel for token in ("voice", "phone", "acs", "tel"))

    async def run_specialist(
        self,
        agent_name: str,
        *,
        message: str,
        context: dict[str, Any],
        attachments: list[dict[str, Any]] | None = None,
    ) -> AgentResult:
        """Invoke a single specialist directly, skipping intent classification.

        Used by surfaces that already know which specialist to reach
        (e.g. the Teams bot always targets ``TripPlannerAgent``). Attachment
        gating and hosted-agent dispatch still apply.
        """
        return await self._run_specialist_agent(
            agent_name, message=message, context=context, attachments=attachments,
        )

    async def _run_specialist_agent(
        self,
        agent_name: str,
        message: str,
        context: dict[str, Any],
        *,
        attachments: list[dict[str, Any]] | None = None,
    ) -> AgentResult:
        history_text = self._format_conversation_history(context)
        if self._is_voice_channel(context):
            # Voice: send the history and request without extra formatting
            # constraints so the agent replies as authored in Foundry.
            prompt = (
                "Conversation history:\n"
                f"{history_text}\n\n"
                f"Current user request: {message}"
            )
        elif history_text == "(No prior conversation)":
            # Text channel, first turn: send the message verbatim so the agent
            # responds with the same full detail it does in the Foundry playground.
            prompt = message
        else:
            # Text channel with history: include the history for continuity but
            # do not constrain the response format, so the answer stays detailed.
            prompt = (
                "Conversation history:\n"
                f"{history_text}\n\n"
                f"Current user request: {message}"
            )

        # Attachments are only injected for agents whose Foundry instructions
        # know how to consume them. For any other agent we log and drop them
        # so a stale attachment doesn't quietly poison an unrelated turn.
        if attachments:
            if agent_name in AGENTS_ACCEPTING_ATTACHMENTS:
                prompt = self._prepend_attachments(prompt, attachments)
                logger.info(
                    "attachments_injected agent=%s count=%s",
                    agent_name,
                    len(attachments),
                )
            else:
                logger.warning(
                    "attachments_ignored agent=%s count=%s "
                    "(agent not in AGENTS_ACCEPTING_ATTACHMENTS)",
                    agent_name,
                    len(attachments),
                )

        summary = await self._run_native_specialist(agent_name, prompt)
        return AgentResult(
            agent=agent_name,
            summary=summary,
            confidence=0.9,
        )

    @staticmethod
    def _prepend_attachments(
        prompt: str, attachments: list[dict[str, Any]]
    ) -> str:
        """Front-load attachment text so the agent sees it before the request.

        Each attachment is a dict with at least ``filename`` and ``text`` keys
        (produced by ``AttachmentStore``). Bounded by ``AttachmentStore`` and
        ``attachment_extractor`` size caps, so this cannot balloon the prompt
        unboundedly.
        """
        blocks: list[str] = [
            "The user has attached the following file(s). Treat their contents "
            "as authoritative context (real dates, names, addresses, booking "
            "references). Never contradict a value that appears in an "
            "attachment; if the user's request conflicts with the attachment, "
            "gently confirm which one to use.\n"
        ]
        for att in attachments:
            filename = att.get("filename") or "unnamed"
            text = (att.get("text") or "").strip()
            if not text:
                continue
            blocks.append(f"[ATTACHMENT: {filename}]\n{text}\n[END ATTACHMENT]")
        blocks.append(prompt)
        return "\n\n".join(blocks)

    async def _route_with_orchestrator_agent(self, message: str, context: dict[str, Any]) -> str:
        """Ask the Foundry-defined OrchestratorAgent to classify the request into one route.

        The OrchestratorAgent's routing logic and instructions live in Azure AI
        Foundry; this only forwards the request and parses the returned route
        token. The agent must exist in Foundry — there is no local routing fallback.
        """
        prompt = (
            "Classify this travel request into exactly one route token.\n"
            f"Message: {message}\n"
            f"Context: {context}\n"
            "Valid tokens: FLIGHT_BOOKING, HOLIDAY_PACKAGE, CRUISE, TOUR, INSPIRATION, "
            "POST_BOOKING, CONSULTANT, DEAL_ALERT, TRIP_PLANNER.\n"
            "Answer with the token only."
        )
        reply = await self._run_foundry_agent("OrchestratorAgent", prompt)
        upper = reply.strip().upper()
        if upper in self.ROUTE_TO_AGENT:
            return upper

        for token in self.ROUTE_TO_AGENT:
            if re.search(rf"\b{token}\b", upper):
                return token

        return ""

    async def _run_native_specialist(self, agent_name: str, prompt: str) -> str:
        # The specialist runs exactly as defined in Azure AI Foundry so its
        # instructions, tools, and behavior stay consistent with what is authored
        # there. There is no code-defined fallback: a specialist that is not
        # present in Foundry is a hard error.
        if agent_name in HOSTED_AGENTS:
            return await self._run_hosted_agent(agent_name, prompt)
        return await self._run_foundry_agent(agent_name, prompt)

    async def _run_hosted_agent(self, agent_name: str, prompt: str) -> str:
        """Run a hosted Foundry agent via its dedicated endpoint.

        Hosted agents (``kind: hosted`` in agent.yaml) run their own container
        behind ``/api/projects/<project>/agents/<name>/endpoint/protocols/openai/responses``
        and are NOT reachable via the shared project responses endpoint that
        prompt agents use. We build a per-agent AsyncOpenAI client pointed at
        that path (via ``AIProjectClient.get_openai_client(agent_name=...)``
        with ``allow_preview=True``) and call ``responses.create`` directly.
        """
        self._require_native()
        try:
            client = self._hosted_openai_clients.get(agent_name)
            if client is None:
                if self._hosted_project_client is None:
                    from azure.ai.projects.aio import AIProjectClient  # type: ignore

                    self._hosted_project_client = AIProjectClient(
                        endpoint=self._project_endpoint,
                        credential=self._async_credential,
                        allow_preview=True,
                    )
                client = self._hosted_project_client.get_openai_client(
                    agent_name=agent_name
                )
                self._hosted_openai_clients[agent_name] = client

            # The hosted-agent endpoint identifies the agent (and its model) via
            # the URL, but the OpenAI Responses SDK still requires a ``model``
            # argument. Passing the project's default deployment is safe --
            # the hosted agent uses its own configured model regardless.
            response = await client.responses.create(
                model=self._hosted_model or agent_name,
                input=[{"role": "user", "content": prompt}],
            )
            text = getattr(response, "output_text", "") or ""
            return text.strip()
        except FoundryAgentError:
            raise
        except Exception as exc:
            raise FoundryAgentError(
                f"Hosted Foundry agent '{agent_name}' run failed: {exc}"
            ) from exc

    async def _run_foundry_agent(self, agent_name: str, prompt: str) -> str:
        """Run a prompt agent defined in Azure AI Foundry, resolved by name.

        Binds to the latest published version of the named Foundry prompt agent
        and runs it. Any failure—agent missing, auth, or run error—is raised as
        ``FoundryAgentError`` so misconfiguration is obvious instead of silently
        degrading.
        """
        self._require_native()
        try:
            agent = self._agent_cache.get(agent_name)
            if agent is None:
                # Behavior (instructions, tools, version) comes from the Foundry
                # prompt agent definition so changes made in Foundry are observed
                # here automatically.
                agent = self._foundry_agent_cls(
                    project_endpoint=self._project_endpoint,
                    agent_name=agent_name,
                    credential=self._async_credential,
                )
                self._agent_cache[agent_name] = agent

            result = await agent.run(prompt)
            return str(result).strip()
        except FoundryAgentError:
            raise
        except Exception as exc:
            raise FoundryAgentError(
                f"Foundry agent '{agent_name}' run failed: {exc}"
            ) from exc

    def _format_conversation_history(self, context: dict[str, Any]) -> str:
        """Format conversation history for the agent prompt."""
        history = context.get("history", [])
        if not isinstance(history, list) or not history:
            return "(No prior conversation)"
        
        lines = []
        for turn in history[-6:]:  # Last 6 turns for brevity
            if isinstance(turn, dict):
                role = turn.get("role", "unknown").upper()
                text = turn.get("text", "")
                if text:
                    lines.append(f"{role}: {text}")
        
        return "\n".join(lines) if lines else "(No prior conversation)"
