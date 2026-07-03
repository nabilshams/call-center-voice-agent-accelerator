"""Reusable CRUD client for Azure AI Foundry agents.

Two agent flavors are supported behind a single, domain-agnostic surface:

* **Classic agents** (Assistants-style data-plane) via
  ``azure.ai.agents.aio.AgentsClient``:
  ``create_agent`` / ``get_agent`` / ``get_agent_by_name`` / ``list_agents``
  / ``update_agent`` / ``delete_agent``.

* **Prompt agents** (versioned; visible under the portal's Build > Agents view
  and consumable by ``agent_framework.FoundryAgent(agent_name=...)``) via
  ``azure.ai.projects.aio.AIProjectClient.agents``:
  ``create_prompt_agent`` / ``get_prompt_agent`` / ``list_prompt_agents``
  / ``delete_prompt_agent``.

Prompt agents are keyed by ``name`` (not id) and are immutable per version;
calling ``create_prompt_agent`` with an existing name creates a new version.

Auth defaults to ``DefaultAzureCredential`` (matching the rest of this app).
Pass an explicit ``credential`` for tests. All errors bubble up as
``FoundryAgentManagementError`` for a single, consistent failure type.
"""

from __future__ import annotations

import logging
from typing import Any

from azure.ai.agents.aio import AgentsClient
from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import AgentKind
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from .exceptions import FoundryAgentManagementError
from .models import AgentInfo, AgentSpec, PromptAgentInfo, PromptAgentSpec

logger = logging.getLogger(__name__)


class FoundryAgentManager:
    """Async CRUD wrapper around Azure AI Foundry agents.

    The manager lazily builds an ``AgentsClient`` on first use so it can be
    safely instantiated at import time even before the event loop is running
    (mirrors the pattern used by ``MAFTravelOrchestrator``).
    """

    def __init__(
        self,
        project_endpoint: str,
        *,
        managed_identity_client_id: str | None = None,
        credential: Any = None,
    ):
        if not project_endpoint:
            raise FoundryAgentManagementError("project_endpoint is required")

        self._endpoint = project_endpoint
        self._managed_identity_client_id = managed_identity_client_id or None
        self._credential = credential
        self._own_credential = credential is None
        self._client: AgentsClient | None = None
        self._project_client: AIProjectClient | None = None

    # -- lifecycle -----------------------------------------------------

    async def __aenter__(self) -> "FoundryAgentManager":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self._project_client is not None:
            await self._project_client.close()
            self._project_client = None
        if self._own_credential and self._credential is not None:
            close = getattr(self._credential, "close", None)
            if close is not None:
                await close()
            self._credential = None

    async def _ensure_credential(self) -> Any:
        if self._credential is None:
            # Import here so tests that pass an explicit credential do not
            # need the azure-identity extras installed.
            from azure.identity.aio import DefaultAzureCredential  # type: ignore

            self._credential = DefaultAzureCredential(
                managed_identity_client_id=self._managed_identity_client_id
            )
        return self._credential

    async def _get_client(self) -> AgentsClient:
        if self._client is None:
            credential = await self._ensure_credential()
            self._client = AgentsClient(endpoint=self._endpoint, credential=credential)
        return self._client

    async def _get_project_client(self) -> AIProjectClient:
        if self._project_client is None:
            credential = await self._ensure_credential()
            self._project_client = AIProjectClient(
                endpoint=self._endpoint, credential=credential
            )
        return self._project_client

    # -- CRUD ----------------------------------------------------------

    async def create_agent(self, spec: AgentSpec) -> AgentInfo:
        client = await self._get_client()
        try:
            agent = await client.create_agent(
                model=spec.model,
                name=spec.name,
                description=spec.description,
                instructions=spec.instructions,
                tools=spec.tools or None,
                temperature=spec.temperature,
                top_p=spec.top_p,
                metadata=spec.metadata or None,
            )
        except HttpResponseError as exc:
            raise FoundryAgentManagementError(
                f"create_agent failed for '{spec.name}': {exc.message}"
            ) from exc
        return AgentInfo.from_sdk(agent)

    async def list_agents(self) -> list[AgentInfo]:
        client = await self._get_client()
        try:
            pager = client.list_agents()
            return [AgentInfo.from_sdk(a) async for a in pager]
        except HttpResponseError as exc:
            raise FoundryAgentManagementError(
                f"list_agents failed: {exc.message}"
            ) from exc

    async def get_agent(self, agent_id: str) -> AgentInfo:
        client = await self._get_client()
        try:
            agent = await client.get_agent(agent_id)
        except ResourceNotFoundError as exc:
            raise FoundryAgentManagementError(f"agent not found: {agent_id}") from exc
        except HttpResponseError as exc:
            raise FoundryAgentManagementError(
                f"get_agent failed for '{agent_id}': {exc.message}"
            ) from exc
        return AgentInfo.from_sdk(agent)

    async def get_agent_by_name(self, name: str) -> AgentInfo | None:
        """Find the first agent with the given ``name``, or ``None``.

        Classic agents are not name-unique -- multiple agents can share a
        name. This helper returns the first match (useful for callers that
        maintain unique names by convention). Returns ``None`` when there is
        no match instead of raising, so callers can use it in
        ``get-or-create`` flows.
        """
        for info in await self.list_agents():
            if info.name == name:
                return info
        return None

    async def update_agent(self, agent_id: str, spec: AgentSpec) -> AgentInfo:
        client = await self._get_client()
        try:
            agent = await client.update_agent(
                agent_id,
                model=spec.model,
                name=spec.name,
                description=spec.description,
                instructions=spec.instructions,
                tools=spec.tools or None,
                temperature=spec.temperature,
                top_p=spec.top_p,
                metadata=spec.metadata or None,
            )
        except ResourceNotFoundError as exc:
            raise FoundryAgentManagementError(f"agent not found: {agent_id}") from exc
        except HttpResponseError as exc:
            raise FoundryAgentManagementError(
                f"update_agent failed for '{agent_id}': {exc.message}"
            ) from exc
        return AgentInfo.from_sdk(agent)

    async def delete_agent(self, agent_id: str) -> None:
        client = await self._get_client()
        try:
            await client.delete_agent(agent_id)
        except ResourceNotFoundError as exc:
            raise FoundryAgentManagementError(f"agent not found: {agent_id}") from exc
        except HttpResponseError as exc:
            raise FoundryAgentManagementError(
                f"delete_agent failed for '{agent_id}': {exc.message}"
            ) from exc

    # -- prompt-agent CRUD --------------------------------------------

    @staticmethod
    def _prompt_definition_body(spec: PromptAgentSpec) -> dict[str, Any]:
        """Build the JSON body for ``AgentsOperations.create_version``.

        Tools are passed through as raw dicts to stay consistent with the
        ``AgentSpec`` contract (see the module docstring on models.py).
        """
        definition: dict[str, Any] = {
            "kind": AgentKind.PROMPT.value,
            "model": spec.model,
        }
        if spec.instructions is not None:
            definition["instructions"] = spec.instructions
        if spec.temperature is not None:
            definition["temperature"] = spec.temperature
        if spec.top_p is not None:
            definition["top_p"] = spec.top_p
        if spec.tools:
            definition["tools"] = list(spec.tools)

        body: dict[str, Any] = {"definition": definition}
        if spec.description is not None:
            body["description"] = spec.description
        if spec.metadata:
            body["metadata"] = dict(spec.metadata)
        return body

    async def create_prompt_agent(self, spec: PromptAgentSpec) -> PromptAgentInfo:
        """Create a new prompt agent (or a new version of an existing one).

        Prompt agents are versioned and keyed by ``name``. Calling this with
        a name that already exists produces a new immutable version.
        """
        client = await self._get_project_client()
        try:
            version = await client.agents.create_version(
                spec.name, body=self._prompt_definition_body(spec)
            )
        except HttpResponseError as exc:
            raise FoundryAgentManagementError(
                f"create_prompt_agent failed for '{spec.name}': {exc.message}"
            ) from exc
        return PromptAgentInfo.from_sdk(version)

    async def list_prompt_agents(self) -> list[PromptAgentInfo]:
        client = await self._get_project_client()
        try:
            pager = client.agents.list(kind=AgentKind.PROMPT)
            return [PromptAgentInfo.from_sdk(a) async for a in pager]
        except HttpResponseError as exc:
            raise FoundryAgentManagementError(
                f"list_prompt_agents failed: {exc.message}"
            ) from exc

    async def get_prompt_agent(self, name: str) -> PromptAgentInfo:
        client = await self._get_project_client()
        try:
            details = await client.agents.get(name)
        except ResourceNotFoundError as exc:
            raise FoundryAgentManagementError(f"prompt agent not found: {name}") from exc
        except HttpResponseError as exc:
            raise FoundryAgentManagementError(
                f"get_prompt_agent failed for '{name}': {exc.message}"
            ) from exc
        return PromptAgentInfo.from_sdk(details)

    async def delete_prompt_agent(self, name: str) -> None:
        client = await self._get_project_client()
        try:
            await client.agents.delete(name)
        except ResourceNotFoundError as exc:
            raise FoundryAgentManagementError(f"prompt agent not found: {name}") from exc
        except HttpResponseError as exc:
            raise FoundryAgentManagementError(
                f"delete_prompt_agent failed for '{name}': {exc.message}"
            ) from exc
