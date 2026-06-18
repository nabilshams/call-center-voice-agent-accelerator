"""General information domain API.

Store info, FAQ search, payment options, gift cards, app info, insurance
overview, and travel advisories. Exposed at ``/api/travel-agency/general``.
"""

from __future__ import annotations

from quart import Blueprint, jsonify, request

from .. import store
from ..common import BASE_PREFIX, register_spec_route

general_api = Blueprint("ta_general", __name__, url_prefix=f"{BASE_PREFIX}/general")
register_spec_route(general_api, "general")


@general_api.route("/stores/<city>", methods=["GET"])
async def get_store_info(city: str):
    results = [
        s
        for s in store.STORES
        if store.matches(s["city"], city) or store.matches(s["suburb"], city)
    ]
    return jsonify({"count": len(results), "stores": results})


@general_api.route("/faqs", methods=["GET"])
async def get_faq_answer():
    query = request.args.get("q", "")
    if not query:
        return jsonify({"count": len(store.FAQS), "faqs": store.FAQS})
    lowered = query.lower()
    results = [
        f
        for f in store.FAQS
        if any(kw in lowered for kw in f["keywords"]) or store.matches(f["question"], query)
    ]
    return jsonify({"count": len(results), "faqs": results})


@general_api.route("/payment-options", methods=["GET"])
async def get_payment_options():
    return jsonify(store.PAYMENT_OPTIONS)


@general_api.route("/gift-cards", methods=["GET"])
async def get_gift_card_info():
    return jsonify(store.GIFT_CARDS)


@general_api.route("/app-info", methods=["GET"])
async def get_app_info():
    return jsonify(store.APP_INFO)


@general_api.route("/insurance-overview", methods=["GET"])
async def get_insurance_overview():
    return jsonify({"overview": store.INSURANCE_OVERVIEW})


@general_api.route("/travel-advisory", methods=["GET"])
async def get_travel_advisory():
    destination = request.args.get("destination")
    result = next(
        (a for a in store.TRAVEL_ADVISORIES if store.matches(a["destination"], destination)),
        None,
    )
    if result is None:
        return jsonify({"error": "advisory_not_found", "destination": destination}), 404
    return jsonify(result)
