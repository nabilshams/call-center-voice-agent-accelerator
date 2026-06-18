"""Local travel orchestrator kept as a deterministic fallback path.

This module intentionally stays in the repo even when Foundry orchestration is
primary, so operators can switch back quickly if needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SpecialistResult:
    agent: str
    summary: str
    missing_fields: list[str]
    confidence: float


class TravelOrchestrator:
    """Routes user requests to specialist travel agents and merges outputs."""

    INTENT_RULES = {
        "flight_booking": ["flight", "airline", "depart", "return", "layover", "cabin"],
        "holiday_package": ["package", "bundle", "all-inclusive", "all inclusive"],
        "cruise_discovery": ["cruise", "ship", "cabin deck", "port"],
        "tour_matching": ["tour", "excursion", "activity", "guide", "day trip"],
        "travel_inspiration": ["ideas", "inspiration", "where should", "recommend destination"],
        "deal_alert": ["deal", "discount", "price drop", "alert", "offer"],
        "post_booking_concierge": ["ticket", "confirmation", "change", "cancel", "baggage", "check-in"],
        "consultant_match": ["advisor", "consultant", "agent", "human help", "specialist"],
        "hotel_reservation": ["hotel", "stay", "room", "check-in", "check out", "resort"],
    }

    REQUIRED_FIELDS = {
        "flight_booking": ["origin", "destination", "departure_date", "travelers"],
        "hotel_reservation": ["city", "check_in", "check_out", "guests"],
        "holiday_package": ["destination", "date_range", "budget"],
        "cruise_discovery": ["region", "date_range", "travelers"],
        "tour_matching": ["destination", "travel_dates", "interests"],
        "travel_inspiration": ["season", "budget", "trip_style"],
        "deal_alert": ["route_or_destination", "date_window", "budget"],
        "post_booking_concierge": ["booking_reference", "support_topic"],
        "consultant_match": ["trip_type", "budget", "timeline"],
    }

    async def orchestrate(self, message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        if not message or not message.strip():
            return {
                "spoken_reply": "Please tell me what you want help with for your trip.",
                "clarification_question": "Would you like help with flights, hotels, or both?",
                "selected_agents": [],
                "specialist_outputs": [],
                "confidence": 0.2,
                "next_step": "collect_intent",
            }

        ctx = context or {}
        selected_agents = self._select_agents(message)
        specialist_outputs = [self._run_specialist(agent, ctx) for agent in selected_agents]

        confidence = 0.0
        if specialist_outputs:
            confidence = round(sum(item.confidence for item in specialist_outputs) / len(specialist_outputs), 2)

        missing_fields: list[str] = []
        for item in specialist_outputs:
            for field in item.missing_fields:
                if field not in missing_fields:
                    missing_fields.append(field)

        if missing_fields:
            return {
                "spoken_reply": "I can help with that. I just need a few details first.",
                "clarification_question": self._build_question(missing_fields),
                "selected_agents": selected_agents,
                "specialist_outputs": [
                    {
                        "agent": item.agent,
                        "summary": item.summary,
                        "missing_fields": item.missing_fields,
                        "confidence": item.confidence,
                    }
                    for item in specialist_outputs
                ],
                "confidence": confidence,
                "next_step": "collect_required_fields",
            }

        return {
            "spoken_reply": " ".join(item.summary for item in specialist_outputs),
            "clarification_question": None,
            "selected_agents": selected_agents,
            "specialist_outputs": [
                {
                    "agent": item.agent,
                    "summary": item.summary,
                    "missing_fields": item.missing_fields,
                    "confidence": item.confidence,
                }
                for item in specialist_outputs
            ],
            "confidence": confidence,
            "next_step": "present_options",
        }

    def _select_agents(self, message: str) -> list[str]:
        text = message.lower()
        scored: list[tuple[str, int]] = []
        for agent, keywords in self.INTENT_RULES.items():
            score = sum(1 for term in keywords if term in text)
            if score > 0:
                scored.append((agent, score))

        if not scored:
            return ["travel_inspiration"]

        scored.sort(key=lambda item: item[1], reverse=True)
        top_score = scored[0][1]
        top_agents = [agent for agent, score in scored if score == top_score]
        return top_agents[:2]

    def _run_specialist(self, agent: str, context: dict[str, Any]) -> SpecialistResult:
        required = self.REQUIRED_FIELDS.get(agent, [])
        missing = [field for field in required if not self._has_value(context, field)]

        summaries = {
            "flight_booking": "I can shortlist flight options once origin, destination, dates, and traveler count are confirmed.",
            "hotel_reservation": "I can match hotels by location, dates, room count, and budget preferences.",
            "holiday_package": "I can compare package options based on destination, dates, and budget range.",
            "cruise_discovery": "I can recommend cruises by region, season, and cabin preferences.",
            "tour_matching": "I can suggest tours that match your interests and travel schedule.",
            "travel_inspiration": "I can suggest destinations aligned with your budget, season, and trip style.",
            "deal_alert": "I can set up deal tracking once route and date flexibility are known.",
            "post_booking_concierge": "I can help with post-booking changes, check-in, and baggage guidance.",
            "consultant_match": "I can match you with a specialist consultant based on trip type and timeline.",
        }

        confidence = 0.82 if not missing else 0.55
        return SpecialistResult(
            agent=agent,
            summary=summaries.get(agent, "I can support this travel request."),
            missing_fields=missing,
            confidence=confidence,
        )

    @staticmethod
    def _has_value(context: dict[str, Any], key: str) -> bool:
        value = context.get(key)
        if value is None:
            return False
        if isinstance(value, str) and not value.strip():
            return False
        if isinstance(value, (list, dict)) and not value:
            return False
        return True

    @staticmethod
    def _build_question(missing_fields: list[str]) -> str:
        labels = {
            "origin": "departure city",
            "destination": "destination",
            "departure_date": "departure date",
            "travelers": "number of travelers",
            "city": "city",
            "check_in": "check-in date",
            "check_out": "check-out date",
            "guests": "number of guests",
            "date_range": "travel date range",
            "budget": "budget",
            "region": "cruise region",
            "travel_dates": "travel dates",
            "interests": "interests",
            "season": "preferred season",
            "trip_style": "trip style",
            "route_or_destination": "route or destination",
            "date_window": "date window",
            "booking_reference": "booking reference",
            "support_topic": "support topic",
            "trip_type": "trip type",
            "timeline": "timeline",
        }
        readable = [labels.get(field, field.replace("_", " ")) for field in missing_fields]

        if len(readable) == 1:
            return f"Could you share your {readable[0]}?"
        if len(readable) == 2:
            return f"Could you share your {readable[0]} and {readable[1]}?"
        return f"Could you share your {', '.join(readable[:-1])}, and {readable[-1]}?"
