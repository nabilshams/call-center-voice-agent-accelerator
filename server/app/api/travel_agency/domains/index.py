"""Index domain API.

Lightweight discovery surface: a health check and an index listing every
travel-agency sub-API with its base path and OpenAPI spec URL. Exposed at
``/api/travel-agency``.
"""

from __future__ import annotations

from quart import Blueprint, jsonify

from ..common import BASE_PREFIX, now_iso

index_api = Blueprint("ta_index", __name__, url_prefix=BASE_PREFIX)

# (domain_key, human title) for each importable sub-API.
_DOMAINS = [
    ("flights", "Flights"),
    ("packages", "Holiday Packages"),
    ("cruises", "Cruises"),
    ("tours", "Tours"),
    ("inspiration", "Travel Inspiration"),
    ("deals", "Deals & Alerts"),
    ("bookings", "Post-Booking Concierge"),
    ("consultants", "Consultant Match"),
    ("general", "General Information"),
    ("support", "Support"),
]


@index_api.route("/health", methods=["GET"])
async def health():
    return jsonify({"status": "ok", "demo": True, "timestamp": now_iso()})


@index_api.route("/", methods=["GET"])
async def index():
    """List every sub-API with its base path and OpenAPI spec URL."""
    apis = [
        {
            "domain": key,
            "title": title,
            "base_path": f"{BASE_PREFIX}/{key}",
            "openapi_url": f"{BASE_PREFIX}/{key}/openapi.yaml",
        }
        for key, title in _DOMAINS
    ]
    return jsonify({"service": "Wanderlux - Travel Agency Demo APIs", "apis": apis})
