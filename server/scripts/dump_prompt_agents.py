"""Dump live Foundry prompt agents to local ``AgentDefinition`` YAML files.

Inverse of ``apply_prompt_agents.py``: pulls every prompt agent from the
configured Foundry project and writes one ``src/travel_agency/<AgentName>/agent.yaml``
per agent so they can be tracked in source control, diffed, and re-applied
via ``apply_prompt_agents.py``.

Usage (from repo root)::

    python server/scripts/dump_prompt_agents.py                                        # writes to <repo>/src/travel_agency/
    python server/scripts/dump_prompt_agents.py --target src/travel_agency/
    python server/scripts/dump_prompt_agents.py --agent OrchestratorAgent
    python server/scripts/dump_prompt_agents.py --force                                # overwrite existing files

By default a folder is only created if it doesn't already exist. Pass
``--force`` to overwrite. When an agent's ``model`` equals ``MAF_MODEL``
from the environment it is written back as the placeholder ``${MAF_MODEL}``
so the YAML remains portable across environments.

Env vars required (typically from ``server/.env``):
    MAF_PROJECT_ENDPOINT      - Foundry project endpoint
    MAF_MODEL                 - model deployment name (used for placeholder substitution)
    AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID  - optional, for UAMI auth
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(SERVER_DIR / ".env")
except ImportError:
    pass

from app.foundry import (  # noqa: E402
    FoundryAgentManagementError,
    FoundryAgentManager,
    PromptAgentInfo,
)


# --- YAML helpers ---------------------------------------------------------


def _str_representer(dumper: yaml.SafeDumper, data: str) -> Any:
    """Force multi-line strings to use the ``|`` literal block style."""
    if "\n" in data:
        return dumper.represent_scalar(
            "tag:yaml.org,2002:str", data, style="|"
        )
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


yaml.SafeDumper.add_representer(str, _str_representer)


# --- Naming ---------------------------------------------------------------


_CAMEL_1 = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_2 = re.compile(r"([a-z0-9])([A-Z])")


def _slug(name: str) -> str:
    """Convert an agent name to a snake_case folder name.

    ``FlightBookingAgent`` -> ``flight_booking``
    ``Post-BookingCocierge`` -> ``post_booking_cocierge``
    ``Multi-IntentOrchestrator`` -> ``multi_intent_orchestrator``
    ``GeneralFAQAgent`` -> ``general_faq``
    """
    base = name.strip().replace("-", " ").replace("_", " ")
    base = _CAMEL_1.sub(r"\1 \2", base)
    base = _CAMEL_2.sub(r"\1 \2", base)
    tokens = [t.lower() for t in base.split() if t]
    if tokens and tokens[-1] == "agent":
        tokens = tokens[:-1] or ["agent"]
    return "_".join(tokens)


# --- Rendering ------------------------------------------------------------


# Portal-injected metadata keys that should NOT round-trip into source-
# controlled AgentDefinitions. These are set/updated by the Foundry portal
# UI on every save and would otherwise churn versions on unrelated edits.
_PORTAL_METADATA_KEYS = {"logo", "modified_at"}
_PORTAL_METADATA_PREFIXES = ("microsoft.",)


def _is_portal_metadata_key(key: str) -> bool:
    return key in _PORTAL_METADATA_KEYS or key.startswith(_PORTAL_METADATA_PREFIXES)


def _clean_metadata(raw: dict[str, str]) -> dict[str, str]:
    """Drop portal-only keys and empty ``description`` from metadata."""
    return {
        k: v
        for k, v in raw.items()
        if not _is_portal_metadata_key(k) and not (k == "description" and not v)
    }


def _clean_instructions(text: str) -> str:
    """Right-strip each line so PyYAML can emit ``|`` block-scalar style.

    Portal-edited prompts commonly contain trailing whitespace before the
    newline character. PyYAML falls back to double-quoted style when a
    string is not "safely representable" as a block scalar, and trailing
    whitespace on any line is one of the disqualifiers. Stripping per-line
    trailing whitespace is semantically a no-op for LLM prompts but makes
    the resulting YAML human-diff-friendly.
    """
    return "\n".join(line.rstrip() for line in text.splitlines()).rstrip() + "\n"


def _build_definition(info: PromptAgentInfo) -> dict[str, Any]:
    """Convert live agent state into a serializable AgentDefinition dict."""
    model_env = os.environ.get("MAF_MODEL")
    model = "${MAF_MODEL}" if model_env and info.model == model_env else info.model

    definition: dict[str, Any] = {
        "name": info.name,
        "kind": "prompt",
        "model": model,
    }
    if info.description:
        definition["description"] = info.description
    if info.temperature is not None:
        definition["temperature"] = float(info.temperature)
    if info.top_p is not None:
        definition["top_p"] = float(info.top_p)
    cleaned_metadata = _clean_metadata(info.metadata or {})
    if cleaned_metadata:
        definition["metadata"] = cleaned_metadata
    if info.tools:
        definition["tools"] = list(info.tools)
    if info.instructions:
        definition["instructions"] = _clean_instructions(info.instructions)
    return definition


_HEADER = (
    "# AgentDefinition for the {name} prompt agent.\n"
    "# Schema: https://learn.microsoft.com/azure/foundry/agents/concepts/agent-yaml-reference\n"
    "#\n"
    "# Apply with:  python scripts/apply_prompt_agents.py agents/\n"
    "#\n"
    "# Idempotent: re-running apply only creates a new Foundry version when a\n"
    "# field below actually differs from the live agent. Use --force to always\n"
    "# create a new version.\n\n"
)


def _dump_yaml(definition: dict[str, Any]) -> str:
    body = yaml.safe_dump(
        definition,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=100,
    )
    return _HEADER.format(name=definition["name"]) + body


# --- Main -----------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--target",
        default=str(SERVER_DIR.parent / "src" / "travel_agency"),
        help="Directory to write agent folders into (default: <repo>/src/travel_agency/)",
    )
    parser.add_argument(
        "--agent",
        action="append",
        default=None,
        help="Only dump the named agent(s). Repeatable.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing agent.yaml files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without touching disk.",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    endpoint = os.environ.get("MAF_PROJECT_ENDPOINT")
    if not endpoint:
        print("ERROR: MAF_PROJECT_ENDPOINT is not set", file=sys.stderr)
        return 2

    target = Path(args.target).resolve()
    target.mkdir(parents=True, exist_ok=True)

    filter_names = {n for n in (args.agent or [])}

    manager = FoundryAgentManager(
        project_endpoint=endpoint,
        managed_identity_client_id=os.environ.get(
            "AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID"
        ),
    )
    try:
        try:
            agents = await manager.list_prompt_agents()
        except FoundryAgentManagementError as exc:
            print(f"ERROR: list_prompt_agents failed: {exc}", file=sys.stderr)
            return 1
    finally:
        await manager.close()

    if not agents:
        print("no prompt agents found on this project")
        return 0

    wrote = 0
    skipped = 0
    for info in sorted(agents, key=lambda a: a.name):
        if filter_names and info.name not in filter_names:
            continue

        folder = target / _slug(info.name)
        out_path = folder / "agent.yaml"

        if out_path.exists() and not args.force:
            print(f"[skip]    {info.name:<32} -> {out_path.relative_to(SERVER_DIR)} (exists)")
            skipped += 1
            continue

        definition = _build_definition(info)
        rendered = _dump_yaml(definition)

        if args.dry_run:
            print(f"[dry-run] {info.name:<32} -> {out_path.relative_to(SERVER_DIR)}")
            continue

        folder.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
        print(f"[write]   {info.name:<32} -> {out_path.relative_to(SERVER_DIR)} (v{info.version})")
        wrote += 1

    print(f"\n{wrote} written, {skipped} skipped, {len(agents)} total on server")
    return 0


def main() -> None:
    args = _parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
