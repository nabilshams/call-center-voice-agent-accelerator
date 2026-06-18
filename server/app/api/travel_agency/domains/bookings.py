"""Post-booking concierge domain API.

Retrieve existing bookings, add insurance, and answer pre-trip questions (visa,
baggage, destination tips, check-in, insurance options). Exposed at
``/api/travel-agency/bookings``.
"""

from __future__ import annotations

from quart import Blueprint, jsonify, request

from .. import store
from ..common import BASE_PREFIX, register_spec_route

bookings_api = Blueprint("ta_bookings", __name__, url_prefix=f"{BASE_PREFIX}/bookings")
register_spec_route(bookings_api, "bookings")


@bookings_api.route("/search", methods=["GET"])
async def get_booking_by_email():
    email = request.args.get("email")
    if not email:
        return jsonify({"error": "email_required"}), 400
    results = [b for b in store.BOOKINGS if store.matches(b["email"], email)]
    return jsonify({"count": len(results), "bookings": results})


@bookings_api.route("/visa", methods=["GET"])
async def get_visa_requirements():
    nationality = request.args.get("nationality", "New Zealand")
    destination_country = request.args.get("destination_country")
    result = next(
        (
            v
            for v in store.VISA_REQUIREMENTS
            if store.matches(v["nationality"], nationality)
            and (not destination_country or store.matches(v["destination_country"], destination_country))
        ),
        None,
    )
    if result is None:
        return (
            jsonify(
                {
                    "error": "visa_info_not_found",
                    "nationality": nationality,
                    "destination_country": destination_country,
                }
            ),
            404,
        )
    return jsonify(result)


@bookings_api.route("/baggage", methods=["GET"])
async def get_baggage_policy():
    airline = request.args.get("airline")
    cabin_class = request.args.get("cabin_class", "economy")
    result = next(
        (
            b
            for b in store.BAGGAGE_POLICIES
            if store.matches(b["airline"], airline) and store.matches(b["cabin_class"], cabin_class)
        ),
        None,
    )
    if result is None:
        return jsonify({"error": "baggage_policy_not_found", "airline": airline, "cabin_class": cabin_class}), 404
    return jsonify(result)


@bookings_api.route("/destination-tips", methods=["GET"])
async def get_destination_tips():
    destination = request.args.get("destination")
    travel_month = request.args.get("travel_month")
    result = next(
        (
            t
            for t in store.DESTINATION_TIPS
            if store.matches(t["destination"], destination)
            and (not travel_month or store.matches(t["travel_month"], travel_month))
        ),
        None,
    )
    if result is None:
        return jsonify({"error": "tips_not_found", "destination": destination}), 404
    return jsonify(result)


@bookings_api.route("/checkin", methods=["GET"])
async def get_flight_check_in_info():
    flight_number = request.args.get("flight_number")
    departure_date = request.args.get("departure_date")
    result = next(
        (
            c
            for c in store.CHECKIN_INFO
            if store.matches(c["flight_number"], flight_number)
            and (not departure_date or c["departure_date"] == departure_date)
        ),
        None,
    )
    if result is None:
        return jsonify({"error": "checkin_info_not_found", "flight_number": flight_number}), 404
    return jsonify(result)


@bookings_api.route("/insurance-options", methods=["GET"])
async def get_travel_insurance_options():
    return jsonify({"tiers": store.INSURANCE_TIERS})


@bookings_api.route("/<reference>", methods=["GET"])
async def get_booking_by_reference(reference: str):
    last_name = request.args.get("last_name")
    booking = next(
        (b for b in store.BOOKINGS if b["booking_reference"].upper() == reference.upper()),
        None,
    )
    if booking is None:
        return jsonify({"error": "booking_not_found", "reference": reference}), 404
    # Identity verification: last_name must match when supplied.
    if not last_name or not store.matches(booking["last_name"], last_name):
        return (
            jsonify(
                {
                    "error": "identity_verification_required",
                    "message": "Provide a matching last_name query parameter to view booking details.",
                }
            ),
            403,
        )
    return jsonify(booking)


@bookings_api.route("/<reference>/insurance", methods=["POST"])
async def add_insurance_to_booking(reference: str):
    body = await request.get_json() or {}
    tier_name = body.get("insurance_tier")
    booking = next(
        (b for b in store.BOOKINGS if b["booking_reference"].upper() == reference.upper()),
        None,
    )
    if booking is None:
        return jsonify({"error": "booking_not_found", "reference": reference}), 404
    tier = next((t for t in store.INSURANCE_TIERS if store.matches(t["tier"], tier_name)), None)
    if tier is None:
        return jsonify({"error": "insurance_tier_not_found", "insurance_tier": tier_name}), 404
    booking["insurance"] = tier["tier"]
    booking["total_nzd"] = round(booking["total_nzd"] + tier["price_per_person_nzd"] * booking["passengers"], 2)
    return jsonify({"updated": True, "booking": booking})
