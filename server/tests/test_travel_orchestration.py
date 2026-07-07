"""Integration tests for travel orchestration route behavior."""

import unittest
from unittest.mock import AsyncMock, patch

import server as server_module

foundry_workflow_client = getattr(server_module, "foundry_workflow_client")
local_maf_orchestrator = getattr(server_module, "local_maf_orchestrator")
FoundryWorkflowError = getattr(server_module, "FoundryWorkflowError")
FoundryAgentError = getattr(server_module, "FoundryAgentError")


class TestTravelOrchestration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = server_module.app
        self.app.config["TESTING"] = True

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
        ) as foundry_invoke:
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

    async def test_travel_orchestrate_foundry_not_configured_returns_error(self):
        self.app.config["TRAVEL_ORCHESTRATOR_MODE"] = "foundry"

        with patch.object(foundry_workflow_client, "is_configured", return_value=False):
            async with self.app.test_client() as client:
                response = await client.post(
                    "/travel/orchestrate",
                    json={"message": "Need hotel options in Tokyo", "context": {}},
                )

        self.assertEqual(response.status_code, 503)
        body = await response.get_json()
        self.assertIn("error", body)

    async def test_travel_orchestrate_foundry_failure_returns_error(self):
        self.app.config["TRAVEL_ORCHESTRATOR_MODE"] = "foundry"

        with patch.object(foundry_workflow_client, "is_configured", return_value=True), patch.object(
            foundry_workflow_client,
            "invoke",
            new=AsyncMock(side_effect=FoundryWorkflowError("simulated failure")),
        ) as foundry_invoke:
            async with self.app.test_client() as client:
                response = await client.post(
                    "/travel/orchestrate",
                    json={"message": "I need flight and hotel to Paris", "context": {"origin": "SYD"}},
                )

        self.assertEqual(response.status_code, 502)
        body = await response.get_json()
        self.assertIn("error", body)
        self.assertEqual(foundry_invoke.await_count, 1)

    async def test_travel_orchestrate_unknown_mode_returns_bad_request(self):
        self.app.config["TRAVEL_ORCHESTRATOR_MODE"] = "bogus"

        async with self.app.test_client() as client:
            response = await client.post(
                "/travel/orchestrate",
                json={"message": "Book me a flight to Paris", "context": {}},
            )

        self.assertEqual(response.status_code, 400)
        body = await response.get_json()
        self.assertIn("error", body)

    async def test_travel_orchestrate_maf_mode_uses_local_maf_orchestrator(self):
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

        self.app.config["TRAVEL_ORCHESTRATOR_MODE"] = "maf"

        with patch.object(
            local_maf_orchestrator,
            "orchestrate",
            new=AsyncMock(return_value=expected),
        ) as maf_orchestrate:
            async with self.app.test_client() as client:
                response = await client.post(
                    "/travel/orchestrate",
                    json={"message": "book me flights to Rome", "context": {}},
                )

            self.assertEqual(response.status_code, 200)
            body = await response.get_json()
            self.assertEqual(body["orchestrator_mode"], "maf")
            self.assertEqual(body["workflow_route"], "FLIGHT_BOOKING")
            self.assertEqual(body["selected_agents"], ["FlightBookingAgent"])
            self.assertEqual(maf_orchestrate.await_count, 1)

    async def test_travel_orchestrate_adds_requester_identity_context(self):
        expected = {
            "spoken_reply": "Identity available.",
            "clarification_question": None,
            "selected_agents": ["TripPlannerAgent"],
            "specialist_outputs": [],
            "confidence": 0.9,
            "next_step": "present_options",
            "workflow_route": "TRIP_PLANNER",
            "workflow_trace": [],
        }
        self.app.config["TRAVEL_ORCHESTRATOR_MODE"] = "maf"

        with patch.object(
            local_maf_orchestrator,
            "orchestrate",
            new=AsyncMock(return_value=expected),
        ) as maf_orchestrate:
            async with self.app.test_client() as client:
                response = await client.post(
                    "/travel/orchestrate",
                    json={"message": "who initiated this request?", "context": {}},
                    headers={
                        "X-MS-CLIENT-PRINCIPAL-NAME": "Ada Lovelace",
                        "X-MS-CLIENT-PRINCIPAL-ID": "aad-user-123",
                        "X-MS-CLIENT-PRINCIPAL-IDP": "aad",
                    },
                )

        self.assertEqual(response.status_code, 200)
        _, kwargs = maf_orchestrate.call_args
        context = kwargs["context"]
        self.assertEqual(context["requester_identity"]["display_name"], "Ada Lovelace")
        self.assertEqual(context["requester_identity"]["user_id"], "aad-user-123")
        self.assertEqual(context["agent_identity"]["agent_name"], "TripPlannerAgent")

    async def test_travel_orchestrate_maf_failure_returns_error(self):
        self.app.config["TRAVEL_ORCHESTRATOR_MODE"] = "maf"

        with patch.object(
            local_maf_orchestrator,
            "orchestrate",
            new=AsyncMock(
                side_effect=FoundryAgentError("Azure AI Foundry agents are unavailable")
            ),
        ) as maf_orchestrate:
            async with self.app.test_client() as client:
                response = await client.post(
                    "/travel/orchestrate",
                    json={"message": "help me pick something", "context": {}},
                )

        self.assertEqual(response.status_code, 502)
        body = await response.get_json()
        self.assertIn("error", body)
        self.assertEqual(maf_orchestrate.await_count, 1)


if __name__ == "__main__":
    unittest.main()
