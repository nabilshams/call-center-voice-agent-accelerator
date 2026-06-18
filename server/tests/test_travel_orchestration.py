"""Integration tests for travel orchestration route behavior."""

import unittest
from unittest.mock import AsyncMock, patch

import server as server_module

travel_orchestrator = getattr(server_module, "travel_orchestrator")
foundry_workflow_client = getattr(server_module, "foundry_workflow_client")
maf_workflow_client = getattr(server_module, "maf_workflow_client")
local_maf_orchestrator = getattr(server_module, "local_maf_orchestrator")
FoundryWorkflowError = getattr(server_module, "FoundryWorkflowError")
MAFWorkflowError = getattr(server_module, "MAFWorkflowError")


class TestTravelOrchestration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = server_module.app
        self.app.config["TESTING"] = True

    async def test_travel_orchestrate_local_mode_uses_local_orchestrator(self):
        expected = {
            "spoken_reply": "Local plan ready.",
            "clarification_question": None,
            "selected_agents": ["flight_specialist"],
            "specialist_outputs": [],
            "confidence": 0.88,
            "next_step": "present_options",
        }

        self.app.config["TRAVEL_ORCHESTRATOR_MODE"] = "local"

        with patch.object(
            travel_orchestrator,
            "orchestrate",
            new=AsyncMock(return_value=expected),
        ) as local_orchestrate:
            async with self.app.test_client() as client:
                response = await client.post(
                    "/travel/orchestrate",
                    json={"message": "Book me a flight to Paris", "context": {"origin": "AKL"}},
                )

            self.assertEqual(response.status_code, 200)
            body = await response.get_json()
            self.assertEqual(body["orchestrator_mode"], "local")
            self.assertEqual(body["selected_agents"], ["flight_specialist"])
            self.assertEqual(body["confidence"], 0.88)
            self.assertEqual(local_orchestrate.await_count, 1)

    async def test_travel_orchestrate_foundry_mode_uses_foundry_when_configured(self):
        expected = {
            "spoken_reply": "Foundry plan ready.",
            "clarification_question": None,
            "selected_agents": ["hotel_specialist"],
            "specialist_outputs": [],
            "confidence": 0.91,
            "next_step": "present_options",
        }

        self.app.config["TRAVEL_ORCHESTRATOR_MODE"] = "foundry"

        with patch.object(foundry_workflow_client, "is_configured", return_value=True), patch.object(
            foundry_workflow_client,
            "invoke",
            new=AsyncMock(return_value=expected),
        ) as foundry_invoke, patch.object(
            travel_orchestrator,
            "orchestrate",
            new=AsyncMock(),
        ) as local_orchestrate:
            async with self.app.test_client() as client:
                response = await client.post(
                    "/travel/orchestrate",
                    json={"message": "Need hotel options in Tokyo", "context": {"nights": 3}},
                )

            self.assertEqual(response.status_code, 200)
            body = await response.get_json()
            self.assertEqual(body["orchestrator_mode"], "foundry")
            self.assertEqual(body["selected_agents"], ["hotel_specialist"])
            self.assertEqual(foundry_invoke.await_count, 1)
            self.assertEqual(local_orchestrate.await_count, 0)

    async def test_travel_orchestrate_foundry_failure_falls_back_to_local(self):
        fallback = {
            "spoken_reply": "Fallback local plan.",
            "clarification_question": "What dates are you traveling?",
            "selected_agents": ["flight_specialist", "hotel_specialist"],
            "specialist_outputs": [],
            "confidence": 0.62,
            "next_step": "collect_details",
        }

        self.app.config["TRAVEL_ORCHESTRATOR_MODE"] = "foundry"

        with patch.object(foundry_workflow_client, "is_configured", return_value=True), patch.object(
            foundry_workflow_client,
            "invoke",
            new=AsyncMock(side_effect=FoundryWorkflowError("simulated failure")),
        ) as foundry_invoke, patch.object(
            travel_orchestrator,
            "orchestrate",
            new=AsyncMock(return_value=fallback),
        ) as local_orchestrate:
            async with self.app.test_client() as client:
                response = await client.post(
                    "/travel/orchestrate",
                    json={"message": "I need flight and hotel to Paris", "context": {"origin": "SYD"}},
                )

            self.assertEqual(response.status_code, 200)
            body = await response.get_json()
            self.assertEqual(body["orchestrator_mode"], "local-fallback")
            self.assertEqual(body["clarification_question"], "What dates are you traveling?")
            self.assertEqual(body["selected_agents"], ["flight_specialist", "hotel_specialist"])
            self.assertEqual(foundry_invoke.await_count, 1)
            self.assertEqual(local_orchestrate.await_count, 1)

    async def test_travel_orchestrate_maf_mode_uses_maf_when_configured(self):
        expected = {
            "spoken_reply": "MAF plan ready.",
            "clarification_question": None,
            "selected_agents": ["itinerary_specialist"],
            "specialist_outputs": [],
            "confidence": 0.93,
            "next_step": "present_options",
        }

        self.app.config["TRAVEL_ORCHESTRATOR_MODE"] = "maf"

        with patch.object(maf_workflow_client, "is_configured", return_value=True), patch.object(
            maf_workflow_client,
            "invoke",
            new=AsyncMock(return_value=expected),
        ) as maf_invoke, patch.object(
            travel_orchestrator,
            "orchestrate",
            new=AsyncMock(),
        ) as local_orchestrate:
            async with self.app.test_client() as client:
                response = await client.post(
                    "/travel/orchestrate",
                    json={"message": "Build a trip to Lisbon", "context": {"duration_days": 5}},
                )

            self.assertEqual(response.status_code, 200)
            body = await response.get_json()
            self.assertEqual(body["orchestrator_mode"], "maf")
            self.assertEqual(body["selected_agents"], ["itinerary_specialist"])
            self.assertEqual(maf_invoke.await_count, 1)
            self.assertEqual(local_orchestrate.await_count, 0)

    async def test_travel_orchestrate_maf_failure_falls_back_to_local(self):
        fallback = {
            "spoken_reply": "Local fallback from MAF.",
            "clarification_question": "What is your travel budget?",
            "selected_agents": ["flight_specialist"],
            "specialist_outputs": [],
            "confidence": 0.59,
            "next_step": "collect_details",
        }

        self.app.config["TRAVEL_ORCHESTRATOR_MODE"] = "maf"

        with patch.object(maf_workflow_client, "is_configured", return_value=True), patch.object(
            maf_workflow_client,
            "invoke",
            new=AsyncMock(side_effect=MAFWorkflowError("simulated maf failure")),
        ) as maf_invoke, patch.object(
            travel_orchestrator,
            "orchestrate",
            new=AsyncMock(return_value=fallback),
        ) as local_orchestrate:
            async with self.app.test_client() as client:
                response = await client.post(
                    "/travel/orchestrate",
                    json={"message": "Need a travel package", "context": {"origin": "SIN"}},
                )

            self.assertEqual(response.status_code, 200)
            body = await response.get_json()
            self.assertEqual(body["orchestrator_mode"], "local-fallback")
            self.assertEqual(body["clarification_question"], "What is your travel budget?")
            self.assertEqual(maf_invoke.await_count, 1)
            self.assertEqual(local_orchestrate.await_count, 1)

    async def test_travel_orchestrate_maf_local_mode_uses_local_maf_orchestrator(self):
        expected = {
            "spoken_reply": "I can shortlist flight options once origin, destination, dates, and traveler count are confirmed.",
            "clarification_question": "Could you share your departure city, destination, departure date, and number of travelers?",
            "selected_agents": ["FlightBookingAgent"],
            "specialist_outputs": [
                {
                    "agent": "FlightBookingAgent",
                    "summary": "I can shortlist flight options once origin, destination, dates, and traveler count are confirmed.",
                    "missing_fields": ["origin", "destination", "departure_date", "travelers"],
                    "confidence": 0.58,
                }
            ],
            "confidence": 0.78,
            "next_step": "collect_required_fields",
            "workflow_route": "FLIGHT_BOOKING",
            "workflow_trace": [],
        }

        self.app.config["TRAVEL_ORCHESTRATOR_MODE"] = "maf-local"

        with patch.object(
            local_maf_orchestrator,
            "orchestrate",
            new=AsyncMock(return_value=expected),
        ) as maf_local_orchestrate:
            async with self.app.test_client() as client:
                response = await client.post(
                    "/travel/orchestrate",
                    json={"message": "book me flights to Rome", "context": {}},
                )

            self.assertEqual(response.status_code, 200)
            body = await response.get_json()
            self.assertEqual(body["orchestrator_mode"], "maf-local")
            self.assertEqual(body["workflow_route"], "FLIGHT_BOOKING")
            self.assertEqual(body["selected_agents"], ["FlightBookingAgent"])
            self.assertEqual(maf_local_orchestrate.await_count, 1)

    async def test_travel_orchestrate_maf_local_supports_route_hint(self):
        self.app.config["TRAVEL_ORCHESTRATOR_MODE"] = "maf-local"

        async with self.app.test_client() as client:
            response = await client.post(
                "/travel/orchestrate",
                json={
                    "message": "help me pick something",
                    "context": {"route_hint": "DEAL_ALERT"},
                },
            )

        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(body["orchestrator_mode"], "maf-local")
        self.assertEqual(body["workflow_route"], "DEAL_ALERT")
        self.assertEqual(body["selected_agents"], ["DealAlertAgent"])


if __name__ == "__main__":
    unittest.main()
