"""Loader for Foundry ``AgentDefinition`` YAML files (prompt kind).

Reads YAML files that follow the Microsoft Foundry `AgentSchema`_
``AgentDefinition`` shape (``kind: prompt`` only for now) and produces
``PromptAgentSpec`` objects consumable by ``FoundryAgentManager``.

Definition shape (all fields except ``name``, ``kind`` and ``model``
are optional)::

    name: GeneralFAQAgent
    kind: prompt                     # required by AgentSchema; only 'prompt' supported here
    model: ${MAF_MODEL}              # ${VAR} substituted from env at load time
    description: General FAQ specialist ...
    temperature: 0.2
    top_p: null
    tools: []
    metadata:
      domain: general
      app: travel-agency
    # exactly one of these two:
    instructions: |
      Multi-line inline instructions ...
    instructions_file: prompts/general_faq.md   # relative to this YAML

Discovery accepts either flat ``agents/<name>.yaml`` or the schema-standard
per-agent folder layout ``agents/<name>/agent.yaml``.

The ``spec_matches_existing`` helper implements idempotent-apply: it returns
``True`` when the live agent's latest version already matches the definition
(so the CLI can skip creating a redundant version). Only fields the
definition sets are compared -- unspecified fields (temperature=None, etc.)
are treated as "don't care".

.. _AgentSchema:
   https://learn.microsoft.com/azure/foundry/agents/concepts/agent-yaml-reference
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import PromptAgentInfo, PromptAgentSpec

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")
_SUPPORTED_KINDS = {"prompt"}


class AgentDefinitionError(ValueError):
    """Raised for malformed prompt-agent definition files."""


@dataclass
class LoadedDefinition:
    """A parsed definition paired with its source path (for error reporting)."""

    spec: PromptAgentSpec
    source: Path


def _expand_env(value: str) -> str:
    """Replace ``${VAR}`` tokens in ``value`` with ``os.environ[VAR]``."""

    def repl(match: re.Match[str]) -> str:
        var = match.group(1)
        replacement = os.environ.get(var)
        if replacement is None:
            raise AgentDefinitionError(f"environment variable '{var}' is not set")
        return replacement

    return _ENV_VAR_PATTERN.sub(repl, value)


def _expand(value: Any) -> Any:
    """Recursively apply env-var substitution to strings inside a document."""
    if isinstance(value, str):
        return _expand_env(value)
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


def load_definition(path: str | Path) -> LoadedDefinition:
    """Parse a single YAML AgentDefinition into a ``PromptAgentSpec``.

    Raises ``AgentDefinitionError`` for structural problems, unsupported
    ``kind`` values, and missing env vars.
    """
    definition_path = Path(path).resolve()
    if not definition_path.is_file():
        raise AgentDefinitionError(f"definition not found: {definition_path}")

    with definition_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)

    if not isinstance(raw, dict):
        raise AgentDefinitionError(f"{definition_path}: top-level must be a mapping")

    data = _expand(raw)

    name = data.get("name")
    kind = data.get("kind")
    model = data.get("model")
    if not isinstance(name, str) or not name.strip():
        raise AgentDefinitionError(f"{definition_path}: 'name' is required")
    if not isinstance(kind, str) or not kind.strip():
        raise AgentDefinitionError(f"{definition_path}: 'kind' is required")
    if kind not in _SUPPORTED_KINDS:
        raise AgentDefinitionError(
            f"{definition_path}: unsupported kind '{kind}' "
            f"(supported: {sorted(_SUPPORTED_KINDS)})"
        )
    if not isinstance(model, str) or not model.strip():
        raise AgentDefinitionError(f"{definition_path}: 'model' is required")

    instructions = data.get("instructions")
    instructions_file = data.get("instructions_file")
    if instructions and instructions_file:
        raise AgentDefinitionError(
            f"{definition_path}: specify either 'instructions' or "
            "'instructions_file', not both"
        )
    if instructions_file:
        instructions_path = (definition_path.parent / instructions_file).resolve()
        if not instructions_path.is_file():
            raise AgentDefinitionError(
                f"{definition_path}: instructions_file not found: {instructions_path}"
            )
        instructions = instructions_path.read_text(encoding="utf-8")

    tools = data.get("tools") or []
    if not isinstance(tools, list) or not all(isinstance(t, dict) for t in tools):
        raise AgentDefinitionError(
            f"{definition_path}: 'tools' must be a list of mappings"
        )

    metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in metadata.items()
    ):
        raise AgentDefinitionError(
            f"{definition_path}: 'metadata' must be a mapping of string -> string"
        )

    spec = PromptAgentSpec(
        name=name.strip(),
        model=model.strip(),
        instructions=instructions,
        description=data.get("description"),
        tools=list(tools),
        temperature=data.get("temperature"),
        top_p=data.get("top_p"),
        metadata=dict(metadata),
    )
    return LoadedDefinition(spec=spec, source=definition_path)


def _peek_kind(path: Path) -> str | None:
    """Cheaply read the ``kind:`` field from a definition file, or ``None``.

    Used by ``discover_definitions`` to filter by kind without fully loading
    (and validating) definitions of kinds this loader doesn't support.
    Returns ``None`` if the file isn't a mapping or has no ``kind`` field --
    the caller then falls back to a full load, which will raise a descriptive
    error.
    """
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except yaml.YAMLError:
        return None
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    return kind.strip() if isinstance(kind, str) else None


def discover_definitions(
    paths: list[str | Path],
    *,
    kinds: set[str] | None = None,
) -> list[LoadedDefinition]:
    """Load every AgentDefinition file under the given paths.

    For directories, discovers both layouts:
      - Flat:       ``<dir>/<name>.y*ml``
      - Per-agent:  ``<dir>/<name>/agent.yaml`` (schema-standard)
    Files are loaded directly. Order is deterministic (sorted by resolved path).
    Duplicate paths across discovery rules are deduplicated.

    ``kinds`` filters the discovered files by their ``kind:`` field. Defaults
    to :data:`_SUPPORTED_KINDS` so hosted / workflow definitions co-located
    under ``agents/`` are silently skipped by prompt-only tooling. Pass
    ``kinds=set()`` to disable filtering (every file is loaded and unsupported
    kinds surface as ``AgentDefinitionError``).
    """
    effective_kinds = _SUPPORTED_KINDS if kinds is None else kinds

    seen: set[Path] = set()
    found: list[Path] = []

    def _add(p: Path) -> None:
        resolved = p.resolve()
        if resolved not in seen:
            seen.add(resolved)
            found.append(resolved)

    for entry in paths:
        p = Path(entry)
        if p.is_dir():
            for child in p.glob("*.y*ml"):
                if child.is_file():
                    _add(child)
            for child in p.glob("*/agent.yaml"):
                if child.is_file():
                    _add(child)
        elif p.is_file():
            _add(p)
        else:
            raise AgentDefinitionError(f"path does not exist: {p}")

    if effective_kinds:
        # Peek at each file's ``kind:`` before loading. Files without an
        # explicit ``kind`` (e.g., co-located ``eval.yaml`` next to an
        # ``agent.yaml``) are silently skipped so directory discovery
        # doesn't fail on non-agent YAML that happens to sit alongside a
        # real AgentDefinition. Explicit ``load_definition(path)`` calls
        # still surface ``AgentDefinitionError('kind is required')``.
        found = [
            path for path in found
            if (_peek_kind(path) or "") in effective_kinds
        ]

    return [load_definition(path) for path in sorted(found)]


def _normalize_text(value: str | None) -> str:
    """Normalize a prompt string for cross-source equality checks.

    Strips trailing whitespace from each line and from the string overall.
    Portal edits often introduce trailing spaces before newlines that YAML
    round-trips as-is; without this normalization the same prompt would
    diff spuriously between disk and live.
    """
    if not value:
        return ""
    return "\n".join(line.rstrip() for line in value.splitlines()).rstrip()


# Portal-injected metadata keys that should not influence drift detection.
# The Foundry portal writes these on every save and they are not user-
# authored content. Keep in sync with ``dump_prompt_agents.py``.
_PORTAL_METADATA_KEYS = {"logo", "modified_at"}
_PORTAL_METADATA_PREFIXES = ("microsoft.",)


def _strip_portal_metadata(raw: dict[str, str] | None) -> dict[str, str]:
    if not raw:
        return {}
    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        if key in _PORTAL_METADATA_KEYS:
            continue
        if key.startswith(_PORTAL_METADATA_PREFIXES):
            continue
        if key == "description" and not value:
            continue
        cleaned[key] = value
    return cleaned


def spec_matches_existing(spec: PromptAgentSpec, existing: PromptAgentInfo) -> bool:
    """Return True when the live agent already matches ``spec``.

    Fields the definition does not set (``None`` on the spec) are ignored --
    the live agent's value for those fields is treated as authoritative.
    Strings are compared after stripping trailing whitespace (YAML block
    scalars add a trailing newline that the server may or may not preserve).
    Portal-only metadata keys (``logo``, ``modified_at``, ``microsoft.*``,
    empty ``description``) are stripped from the live view before comparing
    so unrelated portal saves don't look like drift.
    """
    if spec.model and spec.model != (existing.model or ""):
        return False
    if _normalize_text(spec.instructions) != _normalize_text(existing.instructions):
        return False
    if spec.description is not None and _normalize_text(
        spec.description
    ) != _normalize_text(existing.description):
        return False
    if spec.temperature is not None and spec.temperature != existing.temperature:
        return False
    if spec.top_p is not None and spec.top_p != existing.top_p:
        return False
    if spec.tools and list(spec.tools) != list(existing.tools or []):
        return False
    if spec.metadata and dict(spec.metadata) != _strip_portal_metadata(existing.metadata):
        return False
    return True
