"""Flights domain API.

Search flights, inspect fare rules, and create flight bookings. Exposed at
``/api/travel-agency/flights`` as a standalone API with its own OpenAPI spec.
"""

from __future__ import annotations

from quart import Blueprint, jsonify, request

from .. import store
from ..common import BASE_PREFIX, gen_reference, now_iso, register_spec_route

flights_api = Blueprint("ta_flights", __name__, url_prefix=f"{BASE_PREFIX}/flights")
register_spec_route(flights_api, "flights")


@flights_api.route("/search", methods=["GET"])
async def search_flights():
    origin = request.args.get("origin")
    destination = request.args.get("destination")
    departure_date = request.args.get("departure_date")
    cabin_class = request.args.get("cabin_class")

    results = [
        f
        for f in store.FLIGHTS
        if (not origin or store.matches(f["origin"], origin) or store.matches(f["origin_city"], origin))
        and (
            not destination
            or store.matches(f["destination"], destination)
            or store.matches(f["destination_city"], destination)
        )
        and (not departure_date or f["departure_date"] == departure_date)
        and (not cabin_class or store.matches(f["cabin_class"], cabin_class))
    ]
    return jsonify({"count": len(results), "flights": results})


@flights_api.route("/<flight_id>", methods=["GET"])
async def get_flight_details(flight_id: str):
    flight = next((f for f in store.FLIGHTS if f["flight_id"] == flight_id), None)
    if flight is None:
        return jsonify({"error": "flight_not_found", "flight_id": flight_id}), 404
    return jsonify(flight)


@flights_api.route("/<flight_id>/fare-rules", methods=["GET"])
async def get_fare_rules(flight_id: str):
    flight = next((f for f in store.FLIGHTS if f["flight_id"] == flight_id), None)
    if flight is None:
        return jsonify({"error": "flight_not_found", "flight_id": flight_id}), 404
    return jsonify(
        {
            "flight_id": flight_id,
            "cabin_class": flight["cabin_class"],
            "fare_rules": flight["fare_rules"],
        }
    )


@flights_api.route("/bookings", methods=["POST"])
async def create_booking():
    """Create a flight booking (demo - persists only in memory)."""
    body = await request.get_json() or {}
    flight_ids = body.get("flight_ids", [])
    passengers = body.get("passenger_details", [])
    if not flight_ids or not passengers:
        return (
            jsonify(
                {
                    "error": "invalid_request",
                    "message": "flight_ids and passenger_details are required.",
                }
            ),
            400,
        )
    flights = [f for f in store.FLIGHTS if f["flight_id"] in flight_ids]
    if not flights:
        return jsonify({"error": "flight_not_found", "flight_ids": flight_ids}), 404
    pax_count = len(passengers)
    total = round(sum(f["price_nzd"] for f in flights) * pax_count, 2)
    lead = passengers[0]
    contact = body.get("contact_details", {})
    booking = {
        "booking_reference": gen_reference(),
        "last_name": lead.get("last_name", ""),
        "email": contact.get("email"),
        "lead_passenger": f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip(),
        "passengers": pax_count,
        "type": "flight",
        "itinerary": " + ".join(
            f"{f['origin']} to {f['destination']} ({f['airline']})" for f in flights
        ),
        "airline": flights[0]["airline"],
        "flight_number": flights[0]["flight_number"],
        "cabin_class": flights[0]["cabin_class"],
        "departure_date": flights[0]["departure_date"],
        "return_date": flights[-1]["departure_date"] if len(flights) > 1 else None,
        "destination": flights[0]["destination_city"],
        "total_nzd": total,
        "insurance": None,
        "status": "confirmed",
        "created_at": now_iso(),
    }
    store.BOOKINGS.append(booking)
    return jsonify({"created": True, "booking": booking}), 201
