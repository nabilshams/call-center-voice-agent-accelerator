"""Shared helpers for the travel-agency demo APIs.

The demo surface is split by **functionality / domain** (flights, holiday
packages, cruises, tours, travel inspiration, deal alerts, post-booking
concierge, consultant match, general FAQ, and support hand-off tools). Each
domain is its own Quart Blueprint with its own OpenAPI specification, so it can
be imported into Azure API Management as a standalone API. This module holds the
helpers shared across them.

DEMO surface only: all data comes from the in-memory ``store`` seeded from JSON
files. Mutations live only for the lifetime of the server process.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone
from pathlib import Path

from quart import Blueprint, Response

# Common base path for every travel-agency sub-API.
BASE_PREFIX = "/api/travel-agency"

# Directory holding the per-domain OpenAPI specifications.
SPECS_DIR = Path(__file__).parent / "specs"


def now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def gen_reference() -> str:
    """Generate a demo Wanderlux booking reference (e.g. HOT-512904)."""
    return f"HOT-{random.randint(100000, 999999)}"


def gen_token(prefix: str) -> str:
    """Generate a short uppercase demo reference such as ``HOLD-1A2B3C4D``."""
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def register_spec_route(blueprint: Blueprint, domain_key: str) -> None:
    """Add a ``GET /openapi.yaml`` route serving the domain's own spec.

    The spec is served at ``{blueprint.url_prefix}/openapi.yaml`` so each API
    can be imported into APIM directly by URL.
    """

    spec_path = SPECS_DIR / f"{domain_key}.yaml"

    async def _openapi_spec() -> Response:
        return Response(spec_path.read_text(encoding="utf-8"), mimetype="application/yaml")

    # Unique endpoint name per blueprint avoids collisions on registration.
    blueprint.add_url_rule(
        "/openapi.yaml",
        endpoint="openapi_spec",
        view_func=_openapi_spec,
        methods=["GET"],
    )
