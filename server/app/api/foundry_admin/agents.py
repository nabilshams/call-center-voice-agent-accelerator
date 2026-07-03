"""Foundry hosted-agent CRUD API.

Two blueprints, both gated by the same ``X-Admin-Key``/``ADMIN_API_KEY``
mechanism (see below):

* ``foundry_agents_api`` -- **classic** agents at ``/api/foundry/agents``:
    ``POST   /``            -- create an agent from a JSON ``AgentSpec``
    ``GET    /``            -- list agents
    ``GET    /<agent_id>``  -- fetch one agent by id
    ``PUT    /<agent_id>``  -- update an agent
    ``DELETE /<agent_id>``  -- delete an agent

* ``foundry_prompt_agents_api`` -- **prompt** agents (versioned) at
  ``/api/foundry/prompt-agents``. Keyed by name; each POST with an existing
  name creates a new version. No PUT (versions are immutable):
    ``POST   /``          -- create a prompt agent (or new version)
    ``GET    /``          -- list prompt agents
    ``GET    /<name>``    -- fetch one prompt agent (latest version)
    ``DELETE /<name>``    -- delete a prompt agent

Access control. The blueprints are registered only when
``FOUNDRY_ADMIN_ENABLED=true`` (see ``server.py``). Every request must send an
``X-Admin-Key`` header that matches ``ADMIN_API_KEY``. This is a lightweight
gate suitable for admin/provisioning tasks; swap in Entra auth if a stronger
posture is required.

Both blueprints read their ``FoundryAgentManager`` and admin key from
``current_app.config`` -- keys ``FOUNDRY_AGENT_MANAGER`` and ``ADMIN_API_KEY``
-- so tests can inject fakes without touching module state.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any

from quart import Blueprint, current_app, jsonify, request

from app.foundry import (
    AgentSpec,
    FoundryAgentManagementError,
    FoundryAgentManager,
    PromptAgentSpec,
)

logger = logging.getLogger(__name__)

foundry_agents_api = Blueprint("foundry_agents", __name__, url_prefix="/api/foundry/agents")
foundry_prompt_agents_api = Blueprint(
    "foundry_prompt_agents", __name__, url_prefix="/api/foundry/prompt-agents"
)


def _admin_key_ok() -> bool:
    """Constant-time compare of the ``X-Admin-Key`` header against config."""
    expected = current_app.config.get("ADMIN_API_KEY") or ""
    provided = request.headers.get("X-Admin-Key") or ""
    if not expected:
        # Fail closed: if no key is configured, refuse rather than allow all.
        return False
    return hmac.compare_digest(expected, provided)


def _manager() -> FoundryAgentManager | None:
    return current_app.config.get("FOUNDRY_AGENT_MANAGER")


@foundry_agents_api.before_request
async def _require_admin_key():
    if not _admin_key_ok():
        return jsonify({"error": "unauthorized"}), 401
    if _manager() is None:
        return jsonify({"error": "foundry_agent_manager_not_configured"}), 503
    return None


@foundry_prompt_agents_api.before_request
async def _require_admin_key_prompt():
    if not _admin_key_ok():
        return jsonify({"error": "unauthorized"}), 401
    if _manager() is None:
        return jsonify({"error": "foundry_agent_manager_not_configured"}), 503
    return None


def _spec_from_payload(payload: dict[str, Any]) -> AgentSpec:
    """Build an ``AgentSpec`` from a JSON body; raise ``ValueError`` on bad input."""
    name = payload.get("name")
    model = payload.get("model")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("'name' is required and must be a non-empty string")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("'model' is required and must be a non-empty string")

    tools = payload.get("tools") or []
    if not isinstance(tools, list) or not all(isinstance(t, dict) for t in tools):
        raise ValueError("'tools' must be a list of objects")

    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in metadata.items()
    ):
        raise ValueError("'metadata' must be an object of string -> string")

    return AgentSpec(
        name=name.strip(),
        model=model.strip(),
        instructions=payload.get("instructions"),
        description=payload.get("description"),
        tools=tools,
        temperature=payload.get("temperature"),
        top_p=payload.get("top_p"),
        metadata=metadata,
    )


@foundry_agents_api.route("/", methods=["POST"])
async def create_agent():
    payload = await request.get_json(silent=True) or {}
    try:
        spec = _spec_from_payload(payload)
    except ValueError as exc:
        return jsonify({"error": "invalid_request", "message": str(exc)}), 400

    try:
        info = await _manager().create_agent(spec)  # type: ignore[union-attr]
    except FoundryAgentManagementError as exc:
        logger.warning("create_agent failed: %s", exc)
        return jsonify({"error": "foundry_error", "message": str(exc)}), 502
    return jsonify(info.to_dict()), 201


@foundry_agents_api.route("/", methods=["GET"])
async def list_agents():
    try:
        agents = await _manager().list_agents()  # type: ignore[union-attr]
    except FoundryAgentManagementError as exc:
        logger.warning("list_agents failed: %s", exc)
        return jsonify({"error": "foundry_error", "message": str(exc)}), 502
    return jsonify({"count": len(agents), "agents": [a.to_dict() for a in agents]})


@foundry_agents_api.route("/<agent_id>", methods=["GET"])
async def get_agent(agent_id: str):
    try:
        info = await _manager().get_agent(agent_id)  # type: ignore[union-attr]
    except FoundryAgentManagementError as exc:
        message = str(exc)
        status = 404 if "not found" in message.lower() else 502
        return jsonify({"error": "foundry_error", "message": message}), status
    return jsonify(info.to_dict())


@foundry_agents_api.route("/<agent_id>", methods=["PUT"])
async def update_agent(agent_id: str):
    payload = await request.get_json(silent=True) or {}
    try:
        spec = _spec_from_payload(payload)
    except ValueError as exc:
        return jsonify({"error": "invalid_request", "message": str(exc)}), 400

    try:
        info = await _manager().update_agent(agent_id, spec)  # type: ignore[union-attr]
    except FoundryAgentManagementError as exc:
        message = str(exc)
        status = 404 if "not found" in message.lower() else 502
        return jsonify({"error": "foundry_error", "message": message}), status
    return jsonify(info.to_dict())


@foundry_agents_api.route("/<agent_id>", methods=["DELETE"])
async def delete_agent(agent_id: str):
    try:
        await _manager().delete_agent(agent_id)  # type: ignore[union-attr]
    except FoundryAgentManagementError as exc:
        message = str(exc)
        status = 404 if "not found" in message.lower() else 502
        return jsonify({"error": "foundry_error", "message": message}), status
    return "", 204


# -- prompt-agent routes -------------------------------------------------


def _prompt_spec_from_payload(payload: dict[str, Any]) -> PromptAgentSpec:
    """Build a ``PromptAgentSpec`` from JSON; raise ``ValueError`` on bad input."""
    name = payload.get("name")
    model = payload.get("model")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("'name' is required and must be a non-empty string")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("'model' is required and must be a non-empty string")

    tools = payload.get("tools") or []
    if not isinstance(tools, list) or not all(isinstance(t, dict) for t in tools):
        raise ValueError("'tools' must be a list of objects")

    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in metadata.items()
    ):
        raise ValueError("'metadata' must be an object of string -> string")

    return PromptAgentSpec(
        name=name.strip(),
        model=model.strip(),
        instructions=payload.get("instructions"),
        description=payload.get("description"),
        tools=tools,
        temperature=payload.get("temperature"),
        top_p=payload.get("top_p"),
        metadata=metadata,
    )


@foundry_prompt_agents_api.route("/", methods=["POST"])
async def create_prompt_agent():
    payload = await request.get_json(silent=True) or {}
    try:
        spec = _prompt_spec_from_payload(payload)
    except ValueError as exc:
        return jsonify({"error": "invalid_request", "message": str(exc)}), 400

    try:
        info = await _manager().create_prompt_agent(spec)  # type: ignore[union-attr]
    except FoundryAgentManagementError as exc:
        logger.warning("create_prompt_agent failed: %s", exc)
        return jsonify({"error": "foundry_error", "message": str(exc)}), 502
    return jsonify(info.to_dict()), 201


@foundry_prompt_agents_api.route("/", methods=["GET"])
async def list_prompt_agents():
    try:
        agents = await _manager().list_prompt_agents()  # type: ignore[union-attr]
    except FoundryAgentManagementError as exc:
        logger.warning("list_prompt_agents failed: %s", exc)
        return jsonify({"error": "foundry_error", "message": str(exc)}), 502
    return jsonify({"count": len(agents), "agents": [a.to_dict() for a in agents]})


@foundry_prompt_agents_api.route("/<name>", methods=["GET"])
async def get_prompt_agent(name: str):
    try:
        info = await _manager().get_prompt_agent(name)  # type: ignore[union-attr]
    except FoundryAgentManagementError as exc:
        message = str(exc)
        status = 404 if "not found" in message.lower() else 502
        return jsonify({"error": "foundry_error", "message": message}), status
    return jsonify(info.to_dict())


@foundry_prompt_agents_api.route("/<name>", methods=["DELETE"])
async def delete_prompt_agent(name: str):
    try:
        await _manager().delete_prompt_agent(name)  # type: ignore[union-attr]
    except FoundryAgentManagementError as exc:
        message = str(exc)
        status = 404 if "not found" in message.lower() else 502
        return jsonify({"error": "foundry_error", "message": message}), status
    return "", 204
