"""Deals domain API.

Browse current deals, get deal details, and save / retrieve deal-alert
preferences. Exposed at ``/api/travel-agency/deals``.
"""

from __future__ import annotations

import uuid

from quart import Blueprint, jsonify, request

from .. import store
from ..common import BASE_PREFIX, now_iso, register_spec_route

deals_api = Blueprint("ta_deals", __name__, url_prefix=f"{BASE_PREFIX}/deals")
register_spec_route(deals_api, "deals")


@deals_api.route("/search", methods=["GET"])
async def get_matching_deals():
    destination = request.args.get("destination")
    deal_type = request.args.get("deal_type")
    max_budget = request.args.get("max_budget", type=float)

    results = [
        d
        for d in store.DEALS
        if (not destination or store.matches(d["destination"], destination))
        and (not deal_type or store.matches(d["deal_type"], deal_type))
        and (max_budget is None or d["price_from_nzd"] <= max_budget)
    ]
    return jsonify({"count": len(results), "deals": results})


@deals_api.route("/alerts", methods=["POST"])
async def save_deal_preferences():
    body = await request.get_json() or {}
    profile = {
        "customer_id": body.get("customer_id") or f"CUST-{uuid.uuid4().hex[:8]}",
        "email": body.get("email"),
        "destinations": body.get("destinations", []),
        "months": body.get("months", []),
        "adults": body.get("adults", 1),
        "children": body.get("children", 0),
        "max_budget_nzd": body.get("max_budget_nzd"),
        "deal_types": body.get("deal_types", []),
        "departure_airport": body.get("departure_airport"),
        "notification_channel": body.get("notification_channel", "email"),
        "created_at": now_iso(),
    }
    store.PREFERENCE_PROFILES.append(profile)
    return jsonify({"saved": True, "profile": profile}), 201


@deals_api.route("/alerts/<customer_id>", methods=["GET"])
async def get_customer_alert_profile(customer_id: str):
    profile = next(
        (
            p
            for p in store.PREFERENCE_PROFILES
            if p.get("customer_id") == customer_id or store.matches(p.get("email"), customer_id)
        ),
        None,
    )
    if profile is None:
        return jsonify({"error": "profile_not_found", "customer_id": customer_id}), 404
    return jsonify(profile)


@deals_api.route("/<deal_id>", methods=["GET"])
async def get_deal_details(deal_id: str):
    deal = next((d for d in store.DEALS if d["deal_id"] == deal_id), None)
    if deal is None:
        return jsonify({"error": "deal_not_found", "deal_id": deal_id}), 404
    return jsonify(deal)
