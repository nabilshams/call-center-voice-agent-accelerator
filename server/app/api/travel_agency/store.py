"""In-memory data store for the travel-agency demo APIs.

Loads JSON seed files from the ``data`` directory once at import time and keeps
them in memory. This is intentionally a lightweight demo store - there is no
real backend, database, or persistence. Mutations (e.g. creating a booking or
saving deal preferences) only live for the lifetime of the process.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).parent / "data"


def _load(filename: str) -> dict[str, Any]:
    with open(_DATA_DIR / filename, encoding="utf-8") as handle:
        return json.load(handle)


# Load all seed data once into memory.
_flights = _load("flights.json")
_packages = _load("packages.json")
_cruises = _load("cruises.json")
_tours = _load("tours.json")
_inspiration = _load("inspiration.json")
_deals = _load("deals.json")
_bookings = _load("bookings.json")
_consultants = _load("consultants.json")
_general = _load("general.json")


# Mutable in-memory collections (reset on process restart).
FLIGHTS: list[dict[str, Any]] = _flights["flights"]
PACKAGES: list[dict[str, Any]] = _packages["packages"]
CRUISE_LINES: list[dict[str, Any]] = _cruises["cruise_lines"]
CRUISES: list[dict[str, Any]] = _cruises["cruises"]
OPERATORS: list[dict[str, Any]] = _tours["operators"]
TOURS: list[dict[str, Any]] = _tours["tours"]
DESTINATIONS: list[dict[str, Any]] = _inspiration["destinations"]
TRENDING: list[dict[str, Any]] = _inspiration["trending"]
ARTICLES: list[dict[str, Any]] = _inspiration["articles"]
DEALS: list[dict[str, Any]] = _deals["deals"]
PREFERENCE_PROFILES: list[dict[str, Any]] = _deals["preference_profiles"]
BOOKINGS: list[dict[str, Any]] = _bookings["bookings"]
VISA_REQUIREMENTS: list[dict[str, Any]] = _bookings["visa_requirements"]
BAGGAGE_POLICIES: list[dict[str, Any]] = _bookings["baggage_policies"]
INSURANCE_TIERS: list[dict[str, Any]] = _bookings["insurance_tiers"]
DESTINATION_TIPS: list[dict[str, Any]] = _bookings["destination_tips"]
CHECKIN_INFO: list[dict[str, Any]] = _bookings["checkin_info"]
STORES: list[dict[str, Any]] = _consultants["stores"]
CONSULTANTS: list[dict[str, Any]] = _consultants["consultants"]
FAQS: list[dict[str, Any]] = _general["faqs"]
PAYMENT_OPTIONS: dict[str, Any] = _general["payment_options"]
GIFT_CARDS: dict[str, Any] = _general["gift_cards"]
APP_INFO: dict[str, Any] = _general["app_info"]
INSURANCE_OVERVIEW: str = _general["insurance_overview"]
TRAVEL_ADVISORIES: list[dict[str, Any]] = _general["travel_advisories"]


def matches(value: Any, query: str | None) -> bool:
    """Case-insensitive substring match helper for optional query filters."""
    if not query:
        return True
    if value is None:
        return False
    return query.strip().lower() in str(value).strip().lower()
