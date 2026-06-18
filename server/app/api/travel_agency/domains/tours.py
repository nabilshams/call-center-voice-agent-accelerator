"""Tours domain API.

Search guided tours, get tour and operator details, and register tour
enquiries. Exposed at ``/api/travel-agency/tours``.
"""

from __future__ import annotations

from quart import Blueprint, jsonify, request

from .. import store
from ..common import BASE_PREFIX, gen_token, now_iso, register_spec_route

tours_api = Blueprint("ta_tours", __name__, url_prefix=f"{BASE_PREFIX}/tours")
register_spec_route(tours_api, "tours")


@tours_api.route("/search", methods=["GET"])
async def search_tours():
    region = request.args.get("region")
    style = request.args.get("style")
    fitness_level = request.args.get("fitness_level")
    duration_min = request.args.get("duration_min", type=int)
    duration_max = request.args.get("duration_max", type=int)

    results = [
        t
        for t in store.TOURS
        if (not region or store.matches(t["region"], region))
        and (not style or store.matches(t["style"], style))
        and (not fitness_level or store.matches(t["fitness_level"], fitness_level))
        and (duration_min is None or t["duration_days"] >= duration_min)
        and (duration_max is None or t["duration_days"] <= duration_max)
    ]
    return jsonify({"count": len(results), "tours": results})


@tours_api.route("/operators/<operator_name>", methods=["GET"])
async def get_operator_profile(operator_name: str):
    operator = next(
        (o for o in store.OPERATORS if store.matches(o["operator_name"], operator_name)),
        None,
    )
    if operator is None:
        return jsonify({"error": "operator_not_found", "operator_name": operator_name}), 404
    return jsonify(operator)


@tours_api.route("/<tour_id>", methods=["GET"])
async def get_tour_details(tour_id: str):
    tour = next((t for t in store.TOURS if t["tour_id"] == tour_id), None)
    if tour is None:
        return jsonify({"error": "tour_not_found", "tour_id": tour_id}), 404
    return jsonify(tour)


@tours_api.route("/<tour_id>/enquiry", methods=["POST"])
async def create_tour_enquiry(tour_id: str):
    """Register interest in a tour (demo)."""
    body = await request.get_json() or {}
    tour = next((t for t in store.TOURS if t["tour_id"] == tour_id), None)
    if tour is None:
        return jsonify({"error": "tour_not_found", "tour_id": tour_id}), 404
    return jsonify(
        {
            "enquiry_registered": True,
            "enquiry_reference": gen_token("ENQ"),
            "tour_id": tour_id,
            "tour_name": tour["name"],
            "preferred_departure": body.get("preferred_departure"),
            "customer_details": body.get("customer_details", {}),
            "created_at": now_iso(),
        }
    ), 201
