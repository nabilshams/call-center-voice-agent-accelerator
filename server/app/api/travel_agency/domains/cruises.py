"""Cruises domain API.

Search cruises, get cruise and cruise-line details, and place provisional cabin
holds. Exposed at ``/api/travel-agency/cruises``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from quart import Blueprint, jsonify, request

from .. import store
from ..common import BASE_PREFIX, gen_token, register_spec_route

cruises_api = Blueprint("ta_cruises", __name__, url_prefix=f"{BASE_PREFIX}/cruises")
register_spec_route(cruises_api, "cruises")


@cruises_api.route("/search", methods=["GET"])
async def search_cruises():
    departure_port = request.args.get("departure_port")
    region = request.args.get("region")
    duration_min = request.args.get("duration_min", type=int)
    duration_max = request.args.get("duration_max", type=int)

    results = [
        c
        for c in store.CRUISES
        if (not departure_port or store.matches(c["departure_port"], departure_port))
        and (not region or store.matches(c["region"], region))
        and (duration_min is None or c["duration_nights"] >= duration_min)
        and (duration_max is None or c["duration_nights"] <= duration_max)
    ]
    return jsonify({"count": len(results), "cruises": results})


@cruises_api.route("/lines/<cruise_line>", methods=["GET"])
async def get_cruise_line_info(cruise_line: str):
    line = next(
        (c for c in store.CRUISE_LINES if store.matches(c["cruise_line"], cruise_line)),
        None,
    )
    if line is None:
        return jsonify({"error": "cruise_line_not_found", "cruise_line": cruise_line}), 404
    return jsonify(line)


@cruises_api.route("/<cruise_id>", methods=["GET"])
async def get_cruise_itinerary(cruise_id: str):
    cruise = next((c for c in store.CRUISES if c["cruise_id"] == cruise_id), None)
    if cruise is None:
        return jsonify({"error": "cruise_not_found", "cruise_id": cruise_id}), 404
    return jsonify(cruise)


@cruises_api.route("/<cruise_id>/hold", methods=["POST"])
async def book_cruise_hold(cruise_id: str):
    """Place a provisional hold on a cruise cabin (demo)."""
    body = await request.get_json() or {}
    cruise = next((c for c in store.CRUISES if c["cruise_id"] == cruise_id), None)
    if cruise is None:
        return jsonify({"error": "cruise_not_found", "cruise_id": cruise_id}), 404
    cabin_category = body.get("cabin_category", "balcony")
    hold_hours = body.get("hold_hours", 48)
    expires = datetime.now(timezone.utc) + timedelta(hours=hold_hours)
    return jsonify(
        {
            "held": True,
            "hold_reference": gen_token("HOLD"),
            "cruise_id": cruise_id,
            "cabin_category": cabin_category,
            "passenger_count": body.get("passenger_count", 2),
            "price_per_person_nzd": cruise["cabin_pricing"].get(cabin_category),
            "hold_expires_at": expires.isoformat(),
        }
    ), 201
