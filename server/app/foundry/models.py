"""Neutral data shapes for describing and returning Foundry agents.

No travel-agency, MMH, or any other consumer-specific fields. Callers build
``AgentSpec`` objects with whatever instructions / tools / metadata they want
and pass them to ``FoundryAgentManager``. Responses come back as ``AgentInfo``
dataclasses so consumers do not have to depend on the Azure SDK's model types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentSpec:
    """Everything the manager needs to create or update an agent.

    ``tools`` accepts raw dicts in the shape the Foundry Agents API expects
    (e.g. ``{"type": "code_interpreter"}``). Keeping it as plain dicts avoids
    coupling this module to a specific SDK model version.
    """

    name: str
    model: str
    instructions: str | None = None
    description: str | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    temperature: float | None = None
    top_p: float | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class AgentInfo:
    """Normalized view of a Foundry agent returned by the manager."""

    id: str
    name: str | None
    model: str
    description: str | None
    instructions: str | None
    tools: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    created_at: str | None = None

    @classmethod
    def from_sdk(cls, agent: Any) -> "AgentInfo":
        """Build an ``AgentInfo`` from an ``azure.ai.agents.models.Agent``.

        Uses attribute lookups with defaults so this stays resilient to minor
        SDK model changes across preview versions.
        """
        raw_tools = getattr(agent, "tools", None) or []
        tools: list[dict[str, Any]] = []
        for tool in raw_tools:
            if isinstance(tool, dict):
                tools.append(tool)
            else:
                as_dict = getattr(tool, "as_dict", None)
                tools.append(as_dict() if callable(as_dict) else {"type": str(tool)})

        created_at = getattr(agent, "created_at", None)
        return cls(
            id=getattr(agent, "id", ""),
            name=getattr(agent, "name", None),
            model=getattr(agent, "model", ""),
            description=getattr(agent, "description", None),
            instructions=getattr(agent, "instructions", None),
            tools=tools,
            metadata=dict(getattr(agent, "metadata", None) or {}),
            created_at=str(created_at) if created_at is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "model": self.model,
            "description": self.description,
            "instructions": self.instructions,
            "tools": self.tools,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


@dataclass
class PromptAgentSpec:
    """Input for creating or updating a Foundry *prompt* agent.

    Prompt agents are the newer, versioned agent flavor visible under the
    Foundry portal's Build > Agents view. They are keyed by ``name`` (not id);
    every update produces a new immutable version while the name is stable.
    """

    name: str
    model: str
    instructions: str | None = None
    description: str | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    temperature: float | None = None
    top_p: float | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class PromptAgentInfo:
    """Normalized view of a Foundry prompt agent (latest version).

    The primary identifier for prompt agents is ``name``. ``id`` is the id of
    the specific version this object represents (typically the latest).
    """

    name: str
    version: str | None
    id: str | None
    model: str | None
    instructions: str | None
    description: str | None
    tools: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    status: str | None = None
    created_at: str | None = None
    temperature: float | None = None
    top_p: float | None = None

    @classmethod
    def from_sdk(cls, obj: Any) -> "PromptAgentInfo":
        """Build a ``PromptAgentInfo`` from either ``AgentDetails`` or ``AgentVersionDetails``.

        ``AgentDetails`` (returned by ``get`` / ``list``) wraps the latest
        version at ``obj.versions.latest``. ``AgentVersionDetails`` (returned
        by ``create_version``) is used directly. All field access is
        defensive so this stays resilient to minor SDK shape changes.
        """
        versions = getattr(obj, "versions", None)
        version_obj: Any = obj
        if versions is not None:
            latest = getattr(versions, "latest", None)
            if latest is not None:
                version_obj = latest

        definition = getattr(version_obj, "definition", None)
        raw_tools = getattr(definition, "tools", None) or []
        tools: list[dict[str, Any]] = []
        for tool in raw_tools:
            if isinstance(tool, dict):
                tools.append(tool)
            else:
                as_dict = getattr(tool, "as_dict", None)
                tools.append(as_dict() if callable(as_dict) else {"type": str(tool)})

        created_at = getattr(version_obj, "created_at", None)
        return cls(
            name=getattr(version_obj, "name", None) or getattr(obj, "name", ""),
            version=getattr(version_obj, "version", None),
            id=getattr(version_obj, "id", None) or getattr(obj, "id", None),
            model=getattr(definition, "model", None),
            instructions=getattr(definition, "instructions", None),
            description=getattr(version_obj, "description", None),
            tools=tools,
            metadata=dict(getattr(version_obj, "metadata", None) or {}),
            status=getattr(version_obj, "status", None),
            created_at=str(created_at) if created_at is not None else None,
            temperature=getattr(definition, "temperature", None),
            top_p=getattr(definition, "top_p", None),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "id": self.id,
            "model": self.model,
            "instructions": self.instructions,
            "description": self.description,
            "tools": self.tools,
            "metadata": self.metadata,
            "status": self.status,
            "created_at": self.created_at,
            "temperature": self.temperature,
            "top_p": self.top_p,
        }
