"""Reusable Azure AI Foundry building blocks.

Domain-agnostic helpers for talking to a Foundry project. Nothing in this
package knows about the travel-agency demo, MMH, or any other consumer.
Callers compose their own inputs and pass them in.
"""

from .agent_manager import FoundryAgentManager
from .definition import (
    AgentDefinitionError,
    LoadedDefinition,
    discover_definitions,
    load_definition,
    spec_matches_existing,
)
from .exceptions import FoundryAgentManagementError
from .models import AgentInfo, AgentSpec, PromptAgentInfo, PromptAgentSpec

__all__ = [
    "FoundryAgentManager",
    "FoundryAgentManagementError",
    "AgentInfo",
    "AgentSpec",
    "PromptAgentInfo",
    "PromptAgentSpec",
    "LoadedDefinition",
    "AgentDefinitionError",
    "discover_definitions",
    "load_definition",
    "spec_matches_existing",
]
