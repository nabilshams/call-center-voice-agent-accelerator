"""Travel inspiration domain API.

Destination highlights, best time to visit, trending destinations, and a
search over 'Inspire Me' articles. Exposed at
``/api/travel-agency/inspiration``.
"""

from __future__ import annotations

from quart import Blueprint, jsonify, request

from .. import store
from ..common import BASE_PREFIX, register_spec_route

inspiration_api = Blueprint(
    "ta_inspiration", __name__, url_prefix=f"{BASE_PREFIX}/inspiration"
)
register_spec_route(inspiration_api, "inspiration")


@inspiration_api.route("/destinations/<destination>", methods=["GET"])
async def get_destination_highlights(destination: str):
    dest = next(
        (d for d in store.DESTINATIONS if store.matches(d["destination"], destination)),
        None,
    )
    if dest is None:
        return jsonify({"error": "destination_not_found", "destination": destination}), 404
    return jsonify(dest)


@inspiration_api.route("/destinations/<destination>/best-time", methods=["GET"])
async def get_best_time_to_visit(destination: str):
    dest = next(
        (d for d in store.DESTINATIONS if store.matches(d["destination"], destination)),
        None,
    )
    if dest is None:
        return jsonify({"error": "destination_not_found", "destination": destination}), 404
    return jsonify(
        {
            "destination": dest["destination"],
            "best_time_to_visit": dest["best_time_to_visit"],
            "seasonal_breakdown": dest["seasonal_breakdown"],
        }
    )


@inspiration_api.route("/trending", methods=["GET"])
async def get_trending_destinations():
    month = request.args.get("month")
    traveller_type = request.args.get("traveller_type")
    results = [
        t
        for t in store.TRENDING
        if (not month or store.matches(t["month"], month))
        and (not traveller_type or store.matches(t["traveller_type"], traveller_type))
    ]
    return jsonify({"count": len(results), "trending": results})


@inspiration_api.route("/content", methods=["GET"])
async def search_inspiration_content():
    query = request.args.get("q")
    results = [
        a
        for a in store.ARTICLES
        if not query
        or store.matches(a["title"], query)
        or store.matches(a["destination_or_theme"], query)
    ]
    return jsonify({"count": len(results), "articles": results})
