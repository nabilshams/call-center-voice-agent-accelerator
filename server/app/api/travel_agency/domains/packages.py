"""Holiday packages domain API.

Search holiday packages, get package details, and create package bookings.
Exposed at ``/api/travel-agency/packages``.
"""

from __future__ import annotations

from quart import Blueprint, jsonify, request

from .. import store
from ..common import BASE_PREFIX, gen_reference, now_iso, register_spec_route

packages_api = Blueprint("ta_packages", __name__, url_prefix=f"{BASE_PREFIX}/packages")
register_spec_route(packages_api, "packages")


@packages_api.route("/search", methods=["GET"])
async def search_holiday_packages():
    destination = request.args.get("destination")
    board_basis = request.args.get("board_basis")
    budget_min = request.args.get("budget_min", type=float)
    budget_max = request.args.get("budget_max", type=float)

    results = [
        p
        for p in store.PACKAGES
        if (not destination or store.matches(p["destination"], destination))
        and (not board_basis or store.matches(p["board_basis"], board_basis))
        and (budget_min is None or p["price_per_person_nzd"] >= budget_min)
        and (budget_max is None or p["price_per_person_nzd"] <= budget_max)
    ]
    return jsonify({"count": len(results), "packages": results})


@packages_api.route("/<package_id>", methods=["GET"])
async def get_package_details(package_id: str):
    package = next((p for p in store.PACKAGES if p["package_id"] == package_id), None)
    if package is None:
        return jsonify({"error": "package_not_found", "package_id": package_id}), 404
    return jsonify(package)


@packages_api.route("/bookings", methods=["POST"])
async def create_package_booking():
    """Create a holiday package booking (demo - persists only in memory)."""
    body = await request.get_json() or {}
    package_id = body.get("package_id")
    travellers = body.get("traveller_details", [])
    package = next((p for p in store.PACKAGES if p["package_id"] == package_id), None)
    if package is None:
        return jsonify({"error": "package_not_found", "package_id": package_id}), 404
    if not travellers:
        return (
            jsonify({"error": "invalid_request", "message": "traveller_details are required."}),
            400,
        )
    pax_count = len(travellers)
    total = round(package["price_per_person_nzd"] * pax_count, 2)
    lead = travellers[0]
    contact = body.get("contact_details", {})
    booking = {
        "booking_reference": gen_reference(),
        "last_name": lead.get("last_name", ""),
        "email": contact.get("email"),
        "lead_passenger": f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip(),
        "passengers": pax_count,
        "type": "package",
        "itinerary": f"{package['hotel_name']}, {package['nights']} nights",
        "airline": None,
        "flight_number": None,
        "cabin_class": None,
        "departure_date": None,
        "return_date": None,
        "destination": package["destination"],
        "total_nzd": total,
        "insurance": None,
        "status": "confirmed",
        "created_at": now_iso(),
    }
    store.BOOKINGS.append(booking)
    return jsonify({"created": True, "booking": booking}), 201
