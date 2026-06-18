"""Consultant match domain API.

Find stores, match consultants (ranked by rating), and book appointments or
callbacks. Exposed at ``/api/travel-agency/consultants``.
"""

from __future__ import annotations

from quart import Blueprint, jsonify, request

from .. import store
from ..common import BASE_PREFIX, gen_token, now_iso, register_spec_route

consultants_api = Blueprint(
    "ta_consultants", __name__, url_prefix=f"{BASE_PREFIX}/consultants"
)
register_spec_route(consultants_api, "consultants")


@consultants_api.route("/search", methods=["GET"])
async def match_consultant():
    city = request.args.get("city")
    travel_type = request.args.get("travel_type")
    language = request.args.get("language")

    def specialises(consultant: dict, ttype: str | None) -> bool:
        if not ttype:
            return True
        return any(store.matches(s, ttype) for s in consultant["specialisations"])

    def speaks(consultant: dict, lang: str | None) -> bool:
        if not lang:
            return True
        return any(store.matches(s, lang) for s in consultant["languages"])

    results = [
        c
        for c in store.CONSULTANTS
        if (not city or store.matches(c["city"], city))
        and specialises(c, travel_type)
        and speaks(c, language)
    ]
    results = sorted(results, key=lambda c: c["rating"], reverse=True)
    return jsonify({"count": len(results), "consultants": results})


@consultants_api.route("/stores", methods=["GET"])
async def find_nearest_stores():
    city = request.args.get("city")
    results = [
        s
        for s in store.STORES
        if not city or store.matches(s["city"], city) or store.matches(s["suburb"], city)
    ]
    return jsonify({"count": len(results), "stores": results})


@consultants_api.route("/stores/<store_id>/hours", methods=["GET"])
async def get_store_hours(store_id: str):
    result = next((s for s in store.STORES if s["store_id"] == store_id), None)
    if result is None:
        return jsonify({"error": "store_not_found", "store_id": store_id}), 404
    return jsonify({"store_id": store_id, "name": result["name"], "hours": result["hours"]})


@consultants_api.route("/<consultant_id>", methods=["GET"])
async def get_consultant_profile(consultant_id: str):
    consultant = next(
        (c for c in store.CONSULTANTS if c["consultant_id"] == consultant_id), None
    )
    if consultant is None:
        return jsonify({"error": "consultant_not_found", "consultant_id": consultant_id}), 404
    return jsonify(consultant)


@consultants_api.route("/<consultant_id>/appointment", methods=["POST"])
async def book_consultant_appointment(consultant_id: str):
    """Book an appointment with a consultant (demo)."""
    body = await request.get_json() or {}
    consultant = next(
        (c for c in store.CONSULTANTS if c["consultant_id"] == consultant_id), None
    )
    if consultant is None:
        return jsonify({"error": "consultant_not_found", "consultant_id": consultant_id}), 404
    return jsonify(
        {
            "booked": True,
            "appointment_reference": gen_token("APPT"),
            "consultant_id": consultant_id,
            "consultant_name": consultant["name"],
            "appointment_type": body.get("appointment_type", "video"),
            "preferred_datetime": body.get("preferred_datetime"),
            "video_appointment_url": consultant.get("video_appointment_url"),
            "customer_details": body.get("customer_details", {}),
            "created_at": now_iso(),
        }
    ), 201


@consultants_api.route("/<consultant_id>/callback", methods=["POST"])
async def request_callback(consultant_id: str):
    """Request an immediate callback from a consultant (demo)."""
    body = await request.get_json() or {}
    consultant = next(
        (c for c in store.CONSULTANTS if c["consultant_id"] == consultant_id), None
    )
    if consultant is None:
        return jsonify({"error": "consultant_not_found", "consultant_id": consultant_id}), 404
    return jsonify(
        {
            "callback_queued": True,
            "callback_reference": gen_token("CB"),
            "consultant_id": consultant_id,
            "consultant_name": consultant["name"],
            "customer_phone": body.get("customer_phone"),
            "urgency": body.get("urgency", "normal"),
            "created_at": now_iso(),
        }
    ), 201
