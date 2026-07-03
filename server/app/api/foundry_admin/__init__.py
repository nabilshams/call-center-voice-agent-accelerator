"""Generic Foundry admin HTTP surface.

Domain-agnostic Quart blueprints for managing Foundry resources. Exposes
classic hosted-agent CRUD at ``/api/foundry/agents`` and prompt-agent CRUD
at ``/api/foundry/prompt-agents``. Not mounted under any consumer-specific
prefix (travel-agency, MMH, etc.) -- consumers can use either these endpoints
or the underlying ``FoundryAgentManager`` library directly.
"""

from .agents import foundry_agents_api, foundry_prompt_agents_api

BLUEPRINTS = [foundry_agents_api, foundry_prompt_agents_api]

__all__ = ["BLUEPRINTS", "foundry_agents_api", "foundry_prompt_agents_api"]
