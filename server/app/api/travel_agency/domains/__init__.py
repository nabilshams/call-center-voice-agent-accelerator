"""Travel-agency demo APIs, segregated by functionality / domain.

Each module exposes one Quart Blueprint with its own URL prefix and its own
OpenAPI specification (served at ``{prefix}/openapi.yaml``). ``BLUEPRINTS`` is
the full list to register on the app.
"""

from .bookings import bookings_api
from .consultants import consultants_api
from .cruises import cruises_api
from .deals import deals_api
from .flights import flights_api
from .general import general_api
from .index import index_api
from .inspiration import inspiration_api
from .packages import packages_api
from .support import support_api
from .tours import tours_api

# Order: index first, then one entry per domain.
BLUEPRINTS = [
    index_api,
    flights_api,
    packages_api,
    cruises_api,
    tours_api,
    inspiration_api,
    deals_api,
    bookings_api,
    consultants_api,
    general_api,
    support_api,
]

__all__ = [
    "BLUEPRINTS",
    "index_api",
    "flights_api",
    "packages_api",
    "cruises_api",
    "tours_api",
    "inspiration_api",
    "deals_api",
    "bookings_api",
    "consultants_api",
    "general_api",
    "support_api",
]
