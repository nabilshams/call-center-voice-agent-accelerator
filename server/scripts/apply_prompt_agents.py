"""Apply Foundry ``AgentDefinition`` YAML files to a project (idempotent).

Usage (from repo root)::

    python server/scripts/apply_prompt_agents.py src/travel_agency/
    python server/scripts/apply_prompt_agents.py src/travel_agency/GeneralFAQAgent/agent.yaml
    python server/scripts/apply_prompt_agents.py src/travel_agency/ --dry-run
    python server/scripts/apply_prompt_agents.py src/travel_agency/ --force

Reads one or more YAML AgentDefinition files (see
``app/foundry/definition.py`` for the accepted schema), diffs each against
the live prompt agent in the Foundry project, and creates a new version
only when the definition differs. Use ``--force`` to always create a new
version. ``--dry-run`` shows what would change without calling Azure.

Directory discovery finds both layouts:
  - Flat:       ``src/travel_agency/<name>.yaml``
  - Per-agent:  ``src/travel_agency/<AgentName>/agent.yaml``   (schema-standard, preferred)

Env vars required (typically from ``server/.env``):
    MAF_PROJECT_ENDPOINT      - Foundry project endpoint
    MAF_MODEL                 - model deployment name (referenced by ${MAF_MODEL})
    AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID  - optional, for UAMI auth
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(SERVER_DIR / ".env")
except ImportError:
    pass

from app.foundry import (  # noqa: E402
    AgentDefinitionError,
    FoundryAgentManagementError,
    FoundryAgentManager,
    LoadedDefinition,
    discover_definitions,
    spec_matches_existing,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "paths",
        nargs="+",
        help="AgentDefinition YAML files or directories to apply",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without calling Azure",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Create a new version even if the definition matches the live agent",
    )
    return parser.parse_args()


async def _apply(
    definition: LoadedDefinition,
    manager: FoundryAgentManager,
    *,
    dry_run: bool,
    force: bool,
) -> str:
    """Apply one definition; return a one-line status string for the CLI."""
    spec = definition.spec
    try:
        existing = await manager.get_prompt_agent(spec.name)
    except FoundryAgentManagementError as exc:
        if "not found" not in str(exc).lower():
            raise
        existing = None

    if existing is None:
        if dry_run:
            return f"[dry-run] would CREATE {spec.name} (new agent)"
        info = await manager.create_prompt_agent(spec)
        return f"[created] {info.name} v{info.version} (id={info.id})"

    if not force and spec_matches_existing(spec, existing):
        return f"[unchanged] {spec.name} v{existing.version} (matches definition)"

    if dry_run:
        reason = "force" if force else "drift"
        return f"[dry-run] would UPDATE {spec.name} (currently v{existing.version}, reason={reason})"

    info = await manager.create_prompt_agent(spec)
    return f"[updated] {info.name} v{existing.version} -> v{info.version} (id={info.id})"


async def main() -> int:
    args = _parse_args()

    endpoint = os.environ.get("MAF_PROJECT_ENDPOINT", "").strip()
    if not endpoint:
        print("ERROR: MAF_PROJECT_ENDPOINT is not set", file=sys.stderr)
        return 1
    umi_client_id = (
        os.environ.get("AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID", "").strip() or None
    )

    try:
        definitions = discover_definitions(args.paths)
    except AgentDefinitionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not definitions:
        print(
            "No AgentDefinition YAML files found in the provided paths.",
            file=sys.stderr,
        )
        return 2

    print(f"Endpoint:    {endpoint}")
    print(f"Definitions: {len(definitions)}")
    for d in definitions:
        print(f"  - {d.source}")
    print()

    exit_code = 0
    async with FoundryAgentManager(
        endpoint, managed_identity_client_id=umi_client_id
    ) as manager:
        for definition in definitions:
            try:
                status = await _apply(
                    definition, manager, dry_run=args.dry_run, force=args.force
                )
                print(status)
            except FoundryAgentManagementError as exc:
                print(f"[failed]  {definition.spec.name}: {exc}", file=sys.stderr)
                exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
