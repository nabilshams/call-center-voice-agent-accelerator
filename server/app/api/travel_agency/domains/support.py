"""Support domain API.

Shared hand-off tools: escalate a conversation to a human consultant, and
route a request by intent. Exposed at ``/api/travel-agency/support``.

DEMO stubs - these return a generated reference but perform no real routing.
"""

from __future__ import annotations

from quart import Blueprint, jsonify, request

from ..common import BASE_PREFIX, gen_token, now_iso, register_spec_route

support_api = Blueprint("ta_support", __name__, url_prefix=f"{BASE_PREFIX}/support")
register_spec_route(support_api, "support")


@support_api.route("/escalate", methods=["POST"])
async def escalate_to_human():
    """Escalate the current conversation to a human consultant (demo stub)."""
    body = await request.get_json() or {}
    return jsonify(
        {
            "escalated": True,
            "escalation_reference": gen_token("ESC"),
            "session_id": body.get("session_id"),
            "reason": body.get("reason"),
            "urgency": body.get("urgency", "normal"),
            "created_at": now_iso(),
        }
    ), 201


@support_api.route("/route", methods=["POST"])
async def route_request():
    """Route a request to a specialist domain by intent (demo stub)."""
    body = await request.get_json() or {}
    intent = body.get("intent")
    known = {
        "FLIGHT_BOOKING", "HOLIDAY_PACKAGE", "CRUISE", "TOUR", "INSPIRATION",
        "DEAL_ALERT", "POST_BOOKING", "CONSULTANT", "GENERAL",
    }
    return jsonify(
        {
            "routed": True,
            "intent": intent,
            "recognised": intent in known,
            "session_id": body.get("session_id"),
            "created_at": now_iso(),
        }
    )
