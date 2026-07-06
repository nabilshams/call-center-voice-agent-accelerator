"""Orchestrator-side attachment behaviour tests.

Confirms the two guarantees the rest of the system relies on:

  1. Only agents listed in ``AGENTS_ACCEPTING_ATTACHMENTS`` receive the
     attachment blocks in their prompt; every other agent gets the raw
     user message untouched (safety: a stale attachment must never be
     silently forwarded to a slot-fill specialist that has no
     instructions for it).
  2. When they DO receive attachments, the prompt is prepended with the
     ``[ATTACHMENT: filename] ... [END ATTACHMENT]`` blocks in a shape
     the TripPlannerAgent / PostBookingConcierge Foundry instructions
     already know how to consume.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.handler.local_maf_orchestrator import (
    AGENTS_ACCEPTING_ATTACHMENTS,
    HOSTED_AGENTS,
    MAFTravelOrchestrator,
)


def _make_orchestrator() -> MAFTravelOrchestrator:
    # The MAF orchestrator reads config lazily; an empty dict is fine because
    # we bypass every network call via mocks below.
    return MAFTravelOrchestrator({})


class RegistryTests(unittest.TestCase):
    """Guard rails against accidental widening of the attachment surface."""

    def test_trip_planner_is_in_attachment_allowlist(self):
        self.assertIn("TripPlannerAgent", AGENTS_ACCEPTING_ATTACHMENTS)

    def test_post_booking_concierge_is_in_attachment_allowlist(self):
        self.assertIn("Post-BookingCocierge", AGENTS_ACCEPTING_ATTACHMENTS)

    def test_flight_booking_is_NOT_in_attachment_allowlist(self):
        # Regression guard: FlightBookingAgent is a slot-fill agent whose
        # Foundry prompt has no [ATTACHMENT] block handling. If it ever
        # gets added, the Foundry instructions must be updated first --
        # see the comment on AGENTS_ACCEPTING_ATTACHMENTS.
        self.assertNotIn("FlightBookingAgent", AGENTS_ACCEPTING_ATTACHMENTS)

    def test_trip_planner_is_hosted(self):
        self.assertIn("TripPlannerAgent", HOSTED_AGENTS)


class PrependAttachmentsTests(unittest.TestCase):
    """Static helper -- deterministic, no I/O."""

    def test_attachment_block_precedes_user_prompt(self):
        prompt = MAFTravelOrchestrator._prepend_attachments(
            "Please plan a 5-day trip to Lisbon.",
            [{"filename": "boarding-pass.pdf", "text": "Arrival TAP452 08:15"}],
        )

        # Anchor phrase first, then the block, then the original request.
        self.assertLess(
            prompt.index("[ATTACHMENT: boarding-pass.pdf]"),
            prompt.index("Please plan a 5-day trip"),
        )
        self.assertIn("[END ATTACHMENT]", prompt)
        self.assertIn("Arrival TAP452", prompt)

    def test_authoritative_context_preamble_present(self):
        prompt = MAFTravelOrchestrator._prepend_attachments(
            "user message",
            [{"filename": "x.pdf", "text": "y"}],
        )
        # The preamble is what tells the agent to treat attachments as
        # ground truth over its own guesses -- deleting this by accident
        # would silently regress prompt-injection resistance.
        self.assertIn("Treat their contents", prompt)

    def test_empty_text_attachment_is_skipped(self):
        prompt = MAFTravelOrchestrator._prepend_attachments(
            "user message",
            [
                {"filename": "empty.pdf", "text": ""},
                {"filename": "good.pdf", "text": "real content"},
            ],
        )
        self.assertNotIn("[ATTACHMENT: empty.pdf]", prompt)
        self.assertIn("[ATTACHMENT: good.pdf]", prompt)


class SpecialistRoutingTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end (in-process) tests for the gate on the specialist path."""

    async def _run(self, agent_name: str, attachments):
        orch = _make_orchestrator()
        # _run_native_specialist is where the network call would happen; we
        # replace it with a spy that records the actual prompt it received.
        recorded = {}

        async def _fake_native(name, prompt):
            recorded["agent"] = name
            recorded["prompt"] = prompt
            return "ok"

        with patch.object(orch, "_run_native_specialist", side_effect=_fake_native):
            result = await orch.run_specialist(
                agent_name,
                message="Plan my trip.",
                context={"channel": "web"},
                attachments=attachments,
            )
        return result, recorded

    async def test_trip_planner_receives_attachment_block(self):
        result, recorded = await self._run(
            "TripPlannerAgent",
            [{"filename": "boarding.pdf", "text": "TAP452 arrives 08:15"}],
        )

        self.assertEqual(result.agent, "TripPlannerAgent")
        self.assertIn("[ATTACHMENT: boarding.pdf]", recorded["prompt"])
        self.assertIn("TAP452 arrives 08:15", recorded["prompt"])

    async def test_flight_booking_agent_does_NOT_receive_attachment_block(self):
        _, recorded = await self._run(
            "FlightBookingAgent",
            [{"filename": "boarding.pdf", "text": "SECRET-SHOULD-NOT-APPEAR"}],
        )

        self.assertNotIn("[ATTACHMENT:", recorded["prompt"])
        self.assertNotIn("SECRET-SHOULD-NOT-APPEAR", recorded["prompt"])

    async def test_no_attachments_passthrough_yields_bare_message(self):
        _, recorded = await self._run("TripPlannerAgent", None)

        # First turn + no attachments == just the user message, verbatim.
        self.assertEqual(recorded["prompt"], "Plan my trip.")


if __name__ == "__main__":
    unittest.main()
