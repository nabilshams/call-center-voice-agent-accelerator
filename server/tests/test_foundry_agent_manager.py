"""Unit tests for the reusable ``FoundryAgentManager``.

The Azure SDK is mocked so tests run offline without any Foundry project.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.foundry import (
    AgentInfo,
    AgentSpec,
    FoundryAgentManagementError,
    FoundryAgentManager,
    PromptAgentInfo,
    PromptAgentSpec,
)


def _fake_agent(**overrides):
    base = {
        "id": "agt_123",
        "name": "Specialist",
        "model": "gpt-4o-mini",
        "description": "desc",
        "instructions": "do the thing",
        "tools": [],
        "metadata": {"team": "travel"},
        "created_at": 1234567890,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _AsyncIter:
    """Minimal AsyncItemPaged-compatible iterable for list_agents mocking."""

    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        self._idx = 0
        return self

    async def __anext__(self):
        if self._idx >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._idx]
        self._idx += 1
        return item


class FoundryAgentManagerTests(unittest.IsolatedAsyncioTestCase):
    def _make_manager(self, fake_client):
        manager = FoundryAgentManager(
            "https://example.services.ai.azure.com/api/projects/p1",
            credential=MagicMock(),
        )
        manager._client = fake_client  # bypass lazy build in tests
        return manager

    async def test_create_agent_returns_normalized_info(self):
        client = MagicMock()
        client.create_agent = AsyncMock(return_value=_fake_agent())
        manager = self._make_manager(client)

        info = await manager.create_agent(
            AgentSpec(name="Specialist", model="gpt-4o-mini", instructions="do the thing")
        )

        self.assertIsInstance(info, AgentInfo)
        self.assertEqual(info.id, "agt_123")
        self.assertEqual(info.name, "Specialist")
        client.create_agent.assert_awaited_once()
        _, kwargs = client.create_agent.call_args
        self.assertEqual(kwargs["model"], "gpt-4o-mini")
        self.assertEqual(kwargs["name"], "Specialist")
        self.assertEqual(kwargs["instructions"], "do the thing")

    async def test_create_agent_wraps_http_errors(self):
        from azure.core.exceptions import HttpResponseError

        client = MagicMock()
        client.create_agent = AsyncMock(side_effect=HttpResponseError(message="boom"))
        manager = self._make_manager(client)

        with self.assertRaises(FoundryAgentManagementError):
            await manager.create_agent(AgentSpec(name="X", model="gpt-4o-mini"))

    async def test_list_agents_iterates_paged_results(self):
        client = MagicMock()
        client.list_agents = MagicMock(
            return_value=_AsyncIter([_fake_agent(id="a1"), _fake_agent(id="a2", name="Other")])
        )
        manager = self._make_manager(client)

        agents = await manager.list_agents()

        self.assertEqual([a.id for a in agents], ["a1", "a2"])
        self.assertEqual(agents[1].name, "Other")

    async def test_get_agent_by_name_returns_first_match(self):
        client = MagicMock()
        client.list_agents = MagicMock(
            return_value=_AsyncIter(
                [
                    _fake_agent(id="a1", name="Other"),
                    _fake_agent(id="a2", name="Target"),
                    _fake_agent(id="a3", name="Target"),
                ]
            )
        )
        manager = self._make_manager(client)

        info = await manager.get_agent_by_name("Target")

        self.assertIsNotNone(info)
        self.assertEqual(info.id, "a2")

    async def test_get_agent_by_name_returns_none_when_missing(self):
        client = MagicMock()
        client.list_agents = MagicMock(return_value=_AsyncIter([_fake_agent(name="Other")]))
        manager = self._make_manager(client)

        self.assertIsNone(await manager.get_agent_by_name("Nope"))

    async def test_get_agent_not_found_raises_management_error(self):
        from azure.core.exceptions import ResourceNotFoundError

        client = MagicMock()
        client.get_agent = AsyncMock(side_effect=ResourceNotFoundError(message="missing"))
        manager = self._make_manager(client)

        with self.assertRaises(FoundryAgentManagementError):
            await manager.get_agent("agt_missing")

    async def test_update_and_delete_delegate_to_client(self):
        client = MagicMock()
        client.update_agent = AsyncMock(return_value=_fake_agent(id="agt_upd"))
        client.delete_agent = AsyncMock(return_value=None)
        manager = self._make_manager(client)

        info = await manager.update_agent(
            "agt_upd", AgentSpec(name="Updated", model="gpt-4o-mini")
        )
        self.assertEqual(info.id, "agt_upd")
        client.update_agent.assert_awaited_once()

        await manager.delete_agent("agt_upd")
        client.delete_agent.assert_awaited_once_with("agt_upd")

    async def test_requires_project_endpoint(self):
        with self.assertRaises(FoundryAgentManagementError):
            FoundryAgentManager("")

    async def test_close_releases_resources(self):
        client = MagicMock()
        client.close = AsyncMock()
        cred = MagicMock()
        cred.close = AsyncMock()

        manager = FoundryAgentManager(
            "https://example.services.ai.azure.com/api/projects/p1", credential=cred
        )
        manager._client = client
        manager._own_credential = True

        await manager.close()

        client.close.assert_awaited_once()
        cred.close.assert_awaited_once()


def _fake_version(**overrides):
    """Build a fake ``AgentVersionDetails`` for prompt-agent tests."""
    definition = SimpleNamespace(
        kind="prompt",
        model=overrides.pop("model", "gpt-4o-mini"),
        instructions=overrides.pop("instructions", "answer FAQs"),
        temperature=overrides.pop("temperature", None),
        top_p=overrides.pop("top_p", None),
        tools=overrides.pop("tools", []),
    )
    base = {
        "id": "ver_1",
        "name": "GeneralFAQAgent",
        "version": "1",
        "description": "faq",
        "metadata": {"team": "travel"},
        "status": "active",
        "created_at": 1234567890,
        "definition": definition,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_agent_details(**overrides):
    """Build a fake ``AgentDetails`` wrapping a latest version."""
    version = overrides.pop("latest", _fake_version())
    base = {
        "id": overrides.pop("id", "agt_prompt_1"),
        "name": overrides.pop("name", version.name),
        "versions": SimpleNamespace(latest=version),
    }
    return SimpleNamespace(**base)


class FoundryPromptAgentManagerTests(unittest.IsolatedAsyncioTestCase):
    def _make_manager(self, fake_project_client):
        manager = FoundryAgentManager(
            "https://example.services.ai.azure.com/api/projects/p1",
            credential=MagicMock(),
        )
        manager._project_client = fake_project_client  # bypass lazy build
        return manager

    def _project_client_with(self, **agents_methods):
        agents = MagicMock()
        for name, value in agents_methods.items():
            setattr(agents, name, value)
        return SimpleNamespace(agents=agents)

    async def test_create_prompt_agent_returns_normalized_info(self):
        agents = MagicMock()
        agents.create_version = AsyncMock(return_value=_fake_version(id="ver_new"))
        client = SimpleNamespace(agents=agents)
        manager = self._make_manager(client)

        info = await manager.create_prompt_agent(
            PromptAgentSpec(
                name="GeneralFAQAgent",
                model="gpt-4o-mini",
                instructions="answer FAQs",
                temperature=0.2,
                tools=[{"type": "code_interpreter"}],
                metadata={"team": "travel"},
            )
        )

        self.assertIsInstance(info, PromptAgentInfo)
        self.assertEqual(info.name, "GeneralFAQAgent")
        self.assertEqual(info.id, "ver_new")
        self.assertEqual(info.model, "gpt-4o-mini")
        self.assertEqual(info.instructions, "answer FAQs")

        # verify wire body
        agents.create_version.assert_awaited_once()
        args, kwargs = agents.create_version.call_args
        self.assertEqual(args[0], "GeneralFAQAgent")
        body = kwargs["body"]
        self.assertEqual(body["definition"]["kind"], "prompt")
        self.assertEqual(body["definition"]["model"], "gpt-4o-mini")
        self.assertEqual(body["definition"]["instructions"], "answer FAQs")
        self.assertEqual(body["definition"]["temperature"], 0.2)
        self.assertEqual(body["definition"]["tools"], [{"type": "code_interpreter"}])
        self.assertEqual(body["metadata"], {"team": "travel"})
        # top_p not set -> should be absent
        self.assertNotIn("top_p", body["definition"])

    async def test_create_prompt_agent_wraps_http_errors(self):
        from azure.core.exceptions import HttpResponseError

        agents = MagicMock()
        agents.create_version = AsyncMock(side_effect=HttpResponseError(message="boom"))
        manager = self._make_manager(SimpleNamespace(agents=agents))

        with self.assertRaises(FoundryAgentManagementError):
            await manager.create_prompt_agent(
                PromptAgentSpec(name="X", model="gpt-4o-mini")
            )

    async def test_list_prompt_agents_unwraps_latest_version(self):
        from azure.ai.projects.models import AgentKind

        agents = MagicMock()
        pager = _AsyncIter(
            [
                _fake_agent_details(id="a1", latest=_fake_version(id="v1", name="A")),
                _fake_agent_details(id="a2", latest=_fake_version(id="v2", name="B")),
            ]
        )
        agents.list = MagicMock(return_value=pager)
        manager = self._make_manager(SimpleNamespace(agents=agents))

        infos = await manager.list_prompt_agents()

        self.assertEqual([i.name for i in infos], ["A", "B"])
        self.assertEqual([i.id for i in infos], ["v1", "v2"])
        agents.list.assert_called_once_with(kind=AgentKind.PROMPT)

    async def test_get_prompt_agent_unwraps_versions_latest(self):
        agents = MagicMock()
        agents.get = AsyncMock(
            return_value=_fake_agent_details(
                latest=_fake_version(
                    id="ver_current",
                    name="GeneralFAQAgent",
                    version="3",
                    instructions="latest",
                )
            )
        )
        manager = self._make_manager(SimpleNamespace(agents=agents))

        info = await manager.get_prompt_agent("GeneralFAQAgent")

        self.assertEqual(info.name, "GeneralFAQAgent")
        self.assertEqual(info.version, "3")
        self.assertEqual(info.id, "ver_current")
        self.assertEqual(info.instructions, "latest")
        agents.get.assert_awaited_once_with("GeneralFAQAgent")

    async def test_get_prompt_agent_not_found_raises_management_error(self):
        from azure.core.exceptions import ResourceNotFoundError

        agents = MagicMock()
        agents.get = AsyncMock(side_effect=ResourceNotFoundError(message="missing"))
        manager = self._make_manager(SimpleNamespace(agents=agents))

        with self.assertRaises(FoundryAgentManagementError) as ctx:
            await manager.get_prompt_agent("Nope")
        self.assertIn("not found", str(ctx.exception).lower())

    async def test_delete_prompt_agent_delegates_by_name(self):
        agents = MagicMock()
        agents.delete = AsyncMock(return_value=None)
        manager = self._make_manager(SimpleNamespace(agents=agents))

        await manager.delete_prompt_agent("GeneralFAQAgent")

        agents.delete.assert_awaited_once_with("GeneralFAQAgent")

    async def test_close_releases_project_client(self):
        client = MagicMock()
        client.close = AsyncMock()
        project_client = MagicMock()
        project_client.close = AsyncMock()
        cred = MagicMock()
        cred.close = AsyncMock()

        manager = FoundryAgentManager(
            "https://example.services.ai.azure.com/api/projects/p1", credential=cred
        )
        manager._client = client
        manager._project_client = project_client
        manager._own_credential = True

        await manager.close()

        client.close.assert_awaited_once()
        project_client.close.assert_awaited_once()
        cred.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
