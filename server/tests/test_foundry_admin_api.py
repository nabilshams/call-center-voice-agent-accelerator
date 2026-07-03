"""Integration tests for the ``/api/foundry/agents`` admin blueprint.

Tests build a fresh Quart app per case and inject a mocked
``FoundryAgentManager`` -- this avoids depending on the module-level
registration in ``server.py`` (which is gated behind env vars).
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

from quart import Quart

from app.api.foundry_admin import BLUEPRINTS as FOUNDRY_ADMIN_BLUEPRINTS
from app.foundry import AgentInfo, FoundryAgentManagementError, PromptAgentInfo


def _fake_info(**overrides) -> AgentInfo:
    base = dict(
        id="agt_1",
        name="Specialist",
        model="gpt-4o-mini",
        description="desc",
        instructions="do the thing",
        tools=[],
        metadata={},
        created_at="1234567890",
    )
    base.update(overrides)
    return AgentInfo(**base)


def _fake_prompt_info(**overrides) -> PromptAgentInfo:
    base = dict(
        name="GeneralFAQAgent",
        version="1",
        id="ver_1",
        model="gpt-4o-mini",
        instructions="answer FAQs",
        description="faq",
        tools=[],
        metadata={},
        status="active",
        created_at="1234567890",
    )
    base.update(overrides)
    return PromptAgentInfo(**base)


class FoundryAdminApiTests(unittest.IsolatedAsyncioTestCase):
    def _build_app(self, manager, *, admin_key: str = "s3cret"):
        app = Quart(__name__)
        app.config["TESTING"] = True
        app.config["ADMIN_API_KEY"] = admin_key
        app.config["FOUNDRY_AGENT_MANAGER"] = manager
        for bp in FOUNDRY_ADMIN_BLUEPRINTS:
            app.register_blueprint(bp)
        return app

    async def test_missing_admin_key_returns_401(self):
        manager = MagicMock()
        app = self._build_app(manager)
        async with app.test_client() as client:
            resp = await client.get("/api/foundry/agents/")
        self.assertEqual(resp.status_code, 401)

    async def test_empty_admin_key_config_fails_closed(self):
        manager = MagicMock()
        app = self._build_app(manager, admin_key="")
        async with app.test_client() as client:
            resp = await client.get(
                "/api/foundry/agents/", headers={"X-Admin-Key": ""}
            )
        self.assertEqual(resp.status_code, 401)

    async def test_missing_manager_returns_503(self):
        app = Quart(__name__)
        app.config["ADMIN_API_KEY"] = "s3cret"
        # deliberately no FOUNDRY_AGENT_MANAGER
        for bp in FOUNDRY_ADMIN_BLUEPRINTS:
            app.register_blueprint(bp)
        async with app.test_client() as client:
            resp = await client.get(
                "/api/foundry/agents/", headers={"X-Admin-Key": "s3cret"}
            )
        self.assertEqual(resp.status_code, 503)

    async def test_create_agent_validates_required_fields(self):
        manager = MagicMock()
        manager.create_agent = AsyncMock()
        app = self._build_app(manager)
        async with app.test_client() as client:
            resp = await client.post(
                "/api/foundry/agents/",
                headers={"X-Admin-Key": "s3cret"},
                json={"instructions": "only"},
            )
        self.assertEqual(resp.status_code, 400)
        manager.create_agent.assert_not_called()

    async def test_create_agent_returns_201_with_body(self):
        manager = MagicMock()
        manager.create_agent = AsyncMock(return_value=_fake_info(id="agt_new"))
        app = self._build_app(manager)
        async with app.test_client() as client:
            resp = await client.post(
                "/api/foundry/agents/",
                headers={"X-Admin-Key": "s3cret"},
                json={
                    "name": "Specialist",
                    "model": "gpt-4o-mini",
                    "instructions": "do things",
                },
            )
        self.assertEqual(resp.status_code, 201)
        body = await resp.get_json()
        self.assertEqual(body["id"], "agt_new")
        manager.create_agent.assert_awaited_once()

    async def test_create_agent_maps_foundry_errors_to_502(self):
        manager = MagicMock()
        manager.create_agent = AsyncMock(
            side_effect=FoundryAgentManagementError("upstream boom")
        )
        app = self._build_app(manager)
        async with app.test_client() as client:
            resp = await client.post(
                "/api/foundry/agents/",
                headers={"X-Admin-Key": "s3cret"},
                json={"name": "X", "model": "gpt-4o-mini"},
            )
        self.assertEqual(resp.status_code, 502)

    async def test_list_agents_returns_normalized_payload(self):
        manager = MagicMock()
        manager.list_agents = AsyncMock(
            return_value=[_fake_info(id="a1"), _fake_info(id="a2", name="Other")]
        )
        app = self._build_app(manager)
        async with app.test_client() as client:
            resp = await client.get(
                "/api/foundry/agents/", headers={"X-Admin-Key": "s3cret"}
            )
        self.assertEqual(resp.status_code, 200)
        body = await resp.get_json()
        self.assertEqual(body["count"], 2)
        self.assertEqual([a["id"] for a in body["agents"]], ["a1", "a2"])

    async def test_get_agent_maps_not_found_to_404(self):
        manager = MagicMock()
        manager.get_agent = AsyncMock(
            side_effect=FoundryAgentManagementError("agent not found: agt_x")
        )
        app = self._build_app(manager)
        async with app.test_client() as client:
            resp = await client.get(
                "/api/foundry/agents/agt_x", headers={"X-Admin-Key": "s3cret"}
            )
        self.assertEqual(resp.status_code, 404)

    async def test_delete_agent_returns_204(self):
        manager = MagicMock()
        manager.delete_agent = AsyncMock(return_value=None)
        app = self._build_app(manager)
        async with app.test_client() as client:
            resp = await client.delete(
                "/api/foundry/agents/agt_1", headers={"X-Admin-Key": "s3cret"}
            )
        self.assertEqual(resp.status_code, 204)
        manager.delete_agent.assert_awaited_once_with("agt_1")

    # -- prompt-agent routes -----------------------------------------

    async def test_prompt_route_requires_admin_key(self):
        manager = MagicMock()
        app = self._build_app(manager)
        async with app.test_client() as client:
            resp = await client.get("/api/foundry/prompt-agents/")
        self.assertEqual(resp.status_code, 401)

    async def test_create_prompt_agent_validates_required_fields(self):
        manager = MagicMock()
        manager.create_prompt_agent = AsyncMock()
        app = self._build_app(manager)
        async with app.test_client() as client:
            resp = await client.post(
                "/api/foundry/prompt-agents/",
                headers={"X-Admin-Key": "s3cret"},
                json={"instructions": "only"},
            )
        self.assertEqual(resp.status_code, 400)
        manager.create_prompt_agent.assert_not_called()

    async def test_create_prompt_agent_returns_201_with_body(self):
        manager = MagicMock()
        manager.create_prompt_agent = AsyncMock(
            return_value=_fake_prompt_info(name="GeneralFAQAgent", version="1")
        )
        app = self._build_app(manager)
        async with app.test_client() as client:
            resp = await client.post(
                "/api/foundry/prompt-agents/",
                headers={"X-Admin-Key": "s3cret"},
                json={
                    "name": "GeneralFAQAgent",
                    "model": "gpt-4o-mini",
                    "instructions": "answer FAQs",
                },
            )
        self.assertEqual(resp.status_code, 201)
        body = await resp.get_json()
        self.assertEqual(body["name"], "GeneralFAQAgent")
        self.assertEqual(body["version"], "1")
        manager.create_prompt_agent.assert_awaited_once()

    async def test_list_prompt_agents_returns_normalized_payload(self):
        manager = MagicMock()
        manager.list_prompt_agents = AsyncMock(
            return_value=[
                _fake_prompt_info(name="A", id="v1"),
                _fake_prompt_info(name="B", id="v2"),
            ]
        )
        app = self._build_app(manager)
        async with app.test_client() as client:
            resp = await client.get(
                "/api/foundry/prompt-agents/", headers={"X-Admin-Key": "s3cret"}
            )
        self.assertEqual(resp.status_code, 200)
        body = await resp.get_json()
        self.assertEqual(body["count"], 2)
        self.assertEqual([a["name"] for a in body["agents"]], ["A", "B"])

    async def test_get_prompt_agent_maps_not_found_to_404(self):
        manager = MagicMock()
        manager.get_prompt_agent = AsyncMock(
            side_effect=FoundryAgentManagementError("prompt agent not found: Nope")
        )
        app = self._build_app(manager)
        async with app.test_client() as client:
            resp = await client.get(
                "/api/foundry/prompt-agents/Nope", headers={"X-Admin-Key": "s3cret"}
            )
        self.assertEqual(resp.status_code, 404)

    async def test_delete_prompt_agent_returns_204(self):
        manager = MagicMock()
        manager.delete_prompt_agent = AsyncMock(return_value=None)
        app = self._build_app(manager)
        async with app.test_client() as client:
            resp = await client.delete(
                "/api/foundry/prompt-agents/GeneralFAQAgent",
                headers={"X-Admin-Key": "s3cret"},
            )
        self.assertEqual(resp.status_code, 204)
        manager.delete_prompt_agent.assert_awaited_once_with("GeneralFAQAgent")


if __name__ == "__main__":
    unittest.main()
