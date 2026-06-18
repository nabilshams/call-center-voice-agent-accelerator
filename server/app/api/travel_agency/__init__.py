"""Travel agency demo APIs.

The demo surface is split by **functionality / domain** (flights, holiday
packages, cruises, tours, travel inspiration, deal alerts, post-booking
concierge, consultant match, general FAQ, and support hand-off tools). Each
domain is its own Quart Blueprint, with its own URL prefix under
``/api/travel-agency`` and its own OpenAPI specification served at
``{prefix}/openapi.yaml`` for standalone import into Azure API Management.

This is a DEMO surface only: all data is served from the in-memory ``store``
seeded from JSON files in the ``data`` directory. There is no real backend,
GDS, database, or persistence.

Usage in ``server.py``::

    from app.api.travel_agency import BLUEPRINTS
    for bp in BLUEPRINTS:
        app.register_blueprint(bp)
"""

from .domains import BLUEPRINTS

__all__ = ["BLUEPRINTS"]
