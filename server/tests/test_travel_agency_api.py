"""Tier-2 tests for the Wanderlux travel-agency demo HTTP surface.

Every domain blueprint (11 in total) is exercised via Quart's test client so
that route registration, URL prefixes, query/body parsing, seed-data lookups,
error paths, and per-blueprint OpenAPI serving are all covered.

The suite uses ``server as server_module`` because ``server.py`` is where the
blueprints are actually registered onto the app. Mutable seed collections
(``BOOKINGS``, ``PREFERENCE_PROFILES``) are snapshotted in ``asyncSetUp`` and
restored in ``asyncTearDown`` so ``POST`` tests can't leak state into other
tests or subsequent test runs within the same process.
"""

from __future__ import annotations

import unittest

import server as server_module
from app.api.travel_agency import store
from app.api.travel_agency.common import BASE_PREFIX


class _TravelAgencyTestBase(unittest.IsolatedAsyncioTestCase):
    """Shared plumbing: Quart test client + store snapshot/restore."""

    async def asyncSetUp(self):
        self.app = server_module.app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        # Snapshot mutable collections so POST tests don't leak state.
        self._bookings_snapshot = list(store.BOOKINGS)
        self._profiles_snapshot = list(store.PREFERENCE_PROFILES)

    async def asyncTearDown(self):
        store.BOOKINGS[:] = self._bookings_snapshot
        store.PREFERENCE_PROFILES[:] = self._profiles_snapshot


# =========================================================================
# Index domain: /, /health
# =========================================================================


class IndexApiTests(_TravelAgencyTestBase):
    async def test_health_returns_ok(self):
        response = await self.client.get(f"{BASE_PREFIX}/health")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["demo"])
        self.assertIn("timestamp", body)

    async def test_index_lists_all_ten_sub_apis(self):
        response = await self.client.get(f"{BASE_PREFIX}/")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertIn("Wanderlux", body["service"])
        domains = {api["domain"] for api in body["apis"]}
        expected = {
            "flights", "packages", "cruises", "tours", "inspiration",
            "deals", "bookings", "consultants", "general", "support",
        }
        self.assertEqual(domains, expected)

    async def test_index_entries_carry_openapi_url_and_base_path(self):
        response = await self.client.get(f"{BASE_PREFIX}/")
        body = await response.get_json()
        for api in body["apis"]:
            self.assertEqual(api["base_path"], f"{BASE_PREFIX}/{api['domain']}")
            self.assertEqual(api["openapi_url"], f"{BASE_PREFIX}/{api['domain']}/openapi.yaml")


# =========================================================================
# OpenAPI spec routes -- one per domain, served from disk
# =========================================================================


class OpenApiSpecRoutesTests(_TravelAgencyTestBase):
    DOMAINS = [
        "flights", "packages", "cruises", "tours", "inspiration",
        "deals", "bookings", "consultants", "general", "support",
    ]

    async def test_every_domain_serves_openapi_yaml(self):
        for domain in self.DOMAINS:
            with self.subTest(domain=domain):
                response = await self.client.get(f"{BASE_PREFIX}/{domain}/openapi.yaml")
                self.assertEqual(response.status_code, 200, f"{domain} spec missing")
                self.assertEqual(response.mimetype, "application/yaml")
                text = (await response.get_data()).decode("utf-8")
                # Every spec should at least declare openapi + info.
                self.assertIn("openapi", text.lower())


# =========================================================================
# Flights domain
# =========================================================================


class FlightsApiTests(_TravelAgencyTestBase):
    PREFIX = f"{BASE_PREFIX}/flights"

    async def test_search_without_filters_returns_all(self):
        response = await self.client.get(f"{self.PREFIX}/search")
        body = await response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["count"], len(body["flights"]))
        self.assertGreater(body["count"], 0)

    async def test_search_by_origin_iata_code_matches(self):
        response = await self.client.get(f"{self.PREFIX}/search?origin=AKL")
        body = await response.get_json()
        self.assertGreater(body["count"], 0)
        for flight in body["flights"]:
            self.assertIn("AKL", (flight["origin"], flight["origin_city"]))

    async def test_search_by_origin_city_name_matches(self):
        # `search_flights` falls back to `origin_city` -- verify substring match.
        response = await self.client.get(f"{self.PREFIX}/search?origin=Auckland")
        body = await response.get_json()
        self.assertGreater(body["count"], 0)

    async def test_search_by_unknown_origin_returns_empty(self):
        response = await self.client.get(f"{self.PREFIX}/search?origin=ZZZ")
        body = await response.get_json()
        self.assertEqual(body["count"], 0)
        self.assertEqual(body["flights"], [])

    async def test_get_flight_details_happy_path(self):
        response = await self.client.get(f"{self.PREFIX}/FL-AKLSYD-001")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(body["flight_id"], "FL-AKLSYD-001")

    async def test_get_flight_details_not_found(self):
        response = await self.client.get(f"{self.PREFIX}/FL-DOES-NOT-EXIST")
        self.assertEqual(response.status_code, 404)
        body = await response.get_json()
        self.assertEqual(body["error"], "flight_not_found")

    async def test_get_fare_rules_returns_flight_rules(self):
        response = await self.client.get(f"{self.PREFIX}/FL-AKLSYD-001/fare-rules")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(body["flight_id"], "FL-AKLSYD-001")
        self.assertIn("fare_rules", body)
        self.assertIn("changeable", body["fare_rules"])

    async def test_get_fare_rules_not_found(self):
        response = await self.client.get(f"{self.PREFIX}/BOGUS/fare-rules")
        self.assertEqual(response.status_code, 404)

    async def test_create_booking_requires_flight_ids_and_passengers(self):
        response = await self.client.post(f"{self.PREFIX}/bookings", json={})
        self.assertEqual(response.status_code, 400)
        body = await response.get_json()
        self.assertEqual(body["error"], "invalid_request")

    async def test_create_booking_returns_404_for_unknown_flight(self):
        response = await self.client.post(f"{self.PREFIX}/bookings", json={
            "flight_ids": ["FL-UNKNOWN"],
            "passenger_details": [{"first_name": "A", "last_name": "B"}],
        })
        self.assertEqual(response.status_code, 404)
        body = await response.get_json()
        self.assertEqual(body["error"], "flight_not_found")

    async def test_create_booking_persists_and_prices_by_pax_count(self):
        response = await self.client.post(f"{self.PREFIX}/bookings", json={
            "flight_ids": ["FL-AKLSYD-001"],
            "passenger_details": [
                {"first_name": "A", "last_name": "B"},
                {"first_name": "C", "last_name": "D"},
            ],
            "contact_details": {"email": "test@example.com"},
        })
        self.assertEqual(response.status_code, 201)
        body = await response.get_json()
        self.assertTrue(body["created"])
        booking = body["booking"]
        self.assertEqual(booking["passengers"], 2)
        self.assertEqual(booking["total_nzd"], round(389.0 * 2, 2))
        self.assertEqual(booking["status"], "confirmed")
        self.assertEqual(booking["type"], "flight")
        self.assertTrue(booking["booking_reference"].startswith("HOT-"))
        # And it should now be in the store.
        self.assertIn(booking, store.BOOKINGS)


# =========================================================================
# Holiday packages domain
# =========================================================================


class PackagesApiTests(_TravelAgencyTestBase):
    PREFIX = f"{BASE_PREFIX}/packages"

    async def test_search_filters_by_destination(self):
        response = await self.client.get(f"{self.PREFIX}/search?destination=Fiji")
        body = await response.get_json()
        self.assertGreater(body["count"], 0)
        for pkg in body["packages"]:
            self.assertIn("Fiji", pkg["destination"])

    async def test_search_filters_by_budget_min_and_max(self):
        # Fiji Sofitel is 2349; require min=1000, max=3000 -> included.
        response = await self.client.get(
            f"{self.PREFIX}/search?budget_min=1000&budget_max=3000"
        )
        body = await response.get_json()
        for pkg in body["packages"]:
            self.assertGreaterEqual(pkg["price_per_person_nzd"], 1000)
            self.assertLessEqual(pkg["price_per_person_nzd"], 3000)

    async def test_get_package_details_happy_path(self):
        response = await self.client.get(f"{self.PREFIX}/PKG-FIJI-SOFITEL")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(body["package_id"], "PKG-FIJI-SOFITEL")

    async def test_get_package_details_not_found(self):
        response = await self.client.get(f"{self.PREFIX}/PKG-BOGUS")
        self.assertEqual(response.status_code, 404)

    async def test_create_package_booking_requires_travellers(self):
        response = await self.client.post(f"{self.PREFIX}/bookings", json={
            "package_id": "PKG-FIJI-SOFITEL",
            "traveller_details": [],
        })
        self.assertEqual(response.status_code, 400)

    async def test_create_package_booking_unknown_package(self):
        response = await self.client.post(f"{self.PREFIX}/bookings", json={
            "package_id": "PKG-BOGUS",
            "traveller_details": [{"first_name": "A", "last_name": "B"}],
        })
        self.assertEqual(response.status_code, 404)

    async def test_create_package_booking_persists(self):
        response = await self.client.post(f"{self.PREFIX}/bookings", json={
            "package_id": "PKG-FIJI-SOFITEL",
            "traveller_details": [
                {"first_name": "A", "last_name": "B"},
                {"first_name": "C", "last_name": "D"},
            ],
            "contact_details": {"email": "pkg@example.com"},
        })
        self.assertEqual(response.status_code, 201)
        body = await response.get_json()
        booking = body["booking"]
        self.assertEqual(booking["type"], "package")
        self.assertEqual(booking["destination"], "Fiji")
        self.assertEqual(booking["passengers"], 2)
        self.assertEqual(booking["total_nzd"], round(2349.0 * 2, 2))


# =========================================================================
# Cruises domain
# =========================================================================


class CruisesApiTests(_TravelAgencyTestBase):
    PREFIX = f"{BASE_PREFIX}/cruises"

    async def test_search_by_region(self):
        response = await self.client.get(f"{self.PREFIX}/search?region=Pacific")
        body = await response.get_json()
        self.assertGreater(body["count"], 0)
        for cruise in body["cruises"]:
            self.assertIn("pacific", cruise["region"].lower())

    async def test_search_by_duration_range(self):
        response = await self.client.get(
            f"{self.PREFIX}/search?duration_min=10&duration_max=15"
        )
        body = await response.get_json()
        for cruise in body["cruises"]:
            self.assertGreaterEqual(cruise["duration_nights"], 10)
            self.assertLessEqual(cruise["duration_nights"], 15)

    async def test_get_cruise_itinerary_happy_path(self):
        response = await self.client.get(f"{self.PREFIX}/CR-PRINCESS-PAC14")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(body["cruise_id"], "CR-PRINCESS-PAC14")

    async def test_get_cruise_itinerary_not_found(self):
        response = await self.client.get(f"{self.PREFIX}/CR-NOPE")
        self.assertEqual(response.status_code, 404)

    async def test_get_cruise_line_info_case_insensitive(self):
        response = await self.client.get(f"{self.PREFIX}/lines/princess%20cruises")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(body["cruise_line"], "Princess Cruises")

    async def test_get_cruise_line_info_not_found(self):
        response = await self.client.get(f"{self.PREFIX}/lines/NotARealLine")
        self.assertEqual(response.status_code, 404)

    async def test_hold_cruise_returns_reference_and_price(self):
        response = await self.client.post(
            f"{self.PREFIX}/CR-PRINCESS-PAC14/hold",
            json={"cabin_category": "balcony", "passenger_count": 2, "hold_hours": 24},
        )
        self.assertEqual(response.status_code, 201)
        body = await response.get_json()
        self.assertTrue(body["held"])
        self.assertTrue(body["hold_reference"].startswith("HOLD-"))
        self.assertEqual(body["cabin_category"], "balcony")
        # price should match the balcony rate in the seed.
        self.assertEqual(body["price_per_person_nzd"], 3200.0)
        self.assertIn("hold_expires_at", body)

    async def test_hold_cruise_defaults_to_balcony(self):
        response = await self.client.post(
            f"{self.PREFIX}/CR-PRINCESS-PAC14/hold", json={}
        )
        body = await response.get_json()
        self.assertEqual(body["cabin_category"], "balcony")
        self.assertEqual(body["passenger_count"], 2)

    async def test_hold_cruise_not_found(self):
        response = await self.client.post(f"{self.PREFIX}/CR-BOGUS/hold", json={})
        self.assertEqual(response.status_code, 404)


# =========================================================================
# Tours domain
# =========================================================================


class ToursApiTests(_TravelAgencyTestBase):
    PREFIX = f"{BASE_PREFIX}/tours"

    async def test_search_filters_by_region_and_style(self):
        response = await self.client.get(f"{self.PREFIX}/search?region=Asia&style=Cultural")
        body = await response.get_json()
        for tour in body["tours"]:
            self.assertIn("asia", tour["region"].lower())
            self.assertIn("cultural", tour["style"].lower())

    async def test_search_filters_by_fitness_level(self):
        response = await self.client.get(f"{self.PREFIX}/search?fitness_level=moderate")
        body = await response.get_json()
        for tour in body["tours"]:
            self.assertIn("moderate", tour["fitness_level"].lower())

    async def test_get_tour_details_happy_path(self):
        response = await self.client.get(f"{self.PREFIX}/TOUR-GADV-JAPAN11")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(body["tour_id"], "TOUR-GADV-JAPAN11")

    async def test_get_tour_details_not_found(self):
        response = await self.client.get(f"{self.PREFIX}/TOUR-BOGUS")
        self.assertEqual(response.status_code, 404)

    async def test_get_operator_profile_case_insensitive(self):
        response = await self.client.get(f"{self.PREFIX}/operators/contiki")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(body["operator_name"], "Contiki")

    async def test_get_operator_profile_not_found(self):
        response = await self.client.get(f"{self.PREFIX}/operators/NotAnOperator")
        self.assertEqual(response.status_code, 404)

    async def test_tour_enquiry_returns_reference(self):
        response = await self.client.post(
            f"{self.PREFIX}/TOUR-GADV-JAPAN11/enquiry",
            json={"preferred_departure": "2026-04-01", "customer_details": {"email": "x@y.z"}},
        )
        self.assertEqual(response.status_code, 201)
        body = await response.get_json()
        self.assertTrue(body["enquiry_registered"])
        self.assertTrue(body["enquiry_reference"].startswith("ENQ-"))
        self.assertEqual(body["tour_id"], "TOUR-GADV-JAPAN11")

    async def test_tour_enquiry_unknown_tour(self):
        response = await self.client.post(f"{self.PREFIX}/TOUR-BOGUS/enquiry", json={})
        self.assertEqual(response.status_code, 404)


# =========================================================================
# Inspiration domain
# =========================================================================


class InspirationApiTests(_TravelAgencyTestBase):
    PREFIX = f"{BASE_PREFIX}/inspiration"

    async def test_destination_highlights_happy_path(self):
        response = await self.client.get(f"{self.PREFIX}/destinations/Japan")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(body["destination"], "Japan")
        self.assertIn("highlights", body)

    async def test_destination_highlights_not_found(self):
        response = await self.client.get(f"{self.PREFIX}/destinations/Mars")
        self.assertEqual(response.status_code, 404)

    async def test_best_time_to_visit_projects_two_fields(self):
        response = await self.client.get(f"{self.PREFIX}/destinations/Japan/best-time")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(set(body.keys()), {"destination", "best_time_to_visit", "seasonal_breakdown"})

    async def test_best_time_not_found(self):
        response = await self.client.get(f"{self.PREFIX}/destinations/Mars/best-time")
        self.assertEqual(response.status_code, 404)

    async def test_trending_no_filters_returns_all(self):
        response = await self.client.get(f"{self.PREFIX}/trending")
        body = await response.get_json()
        self.assertEqual(body["count"], len(body["trending"]))
        self.assertGreater(body["count"], 0)

    async def test_trending_filtered_by_month_and_type(self):
        response = await self.client.get(
            f"{self.PREFIX}/trending?month=March&traveller_type=couple"
        )
        body = await response.get_json()
        for entry in body["trending"]:
            self.assertEqual(entry["month"].lower(), "march")
            self.assertEqual(entry["traveller_type"].lower(), "couple")

    async def test_content_search_matches_title(self):
        response = await self.client.get(f"{self.PREFIX}/content?q=cherry")
        body = await response.get_json()
        # At least one article about Japan spring / cherry blossoms.
        self.assertGreater(body["count"], 0)

    async def test_content_search_no_query_returns_all_articles(self):
        response = await self.client.get(f"{self.PREFIX}/content")
        body = await response.get_json()
        self.assertEqual(body["count"], len(store.ARTICLES))


# =========================================================================
# Deals domain
# =========================================================================


class DealsApiTests(_TravelAgencyTestBase):
    PREFIX = f"{BASE_PREFIX}/deals"

    async def test_search_by_destination_and_budget(self):
        response = await self.client.get(
            f"{self.PREFIX}/search?destination=Fiji&max_budget=2000"
        )
        body = await response.get_json()
        self.assertGreater(body["count"], 0)
        for deal in body["deals"]:
            self.assertIn("fiji", deal["destination"].lower())
            self.assertLessEqual(deal["price_from_nzd"], 2000)

    async def test_deal_details_happy_path(self):
        response = await self.client.get(f"{self.PREFIX}/DEAL-FIJI-001")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(body["deal_id"], "DEAL-FIJI-001")

    async def test_deal_details_not_found(self):
        response = await self.client.get(f"{self.PREFIX}/DEAL-BOGUS")
        self.assertEqual(response.status_code, 404)

    async def test_save_alert_generates_customer_id_when_missing(self):
        response = await self.client.post(f"{self.PREFIX}/alerts", json={
            "email": "alert@example.com",
            "destinations": ["Fiji"],
            "max_budget_nzd": 2500,
        })
        self.assertEqual(response.status_code, 201)
        body = await response.get_json()
        self.assertTrue(body["saved"])
        profile = body["profile"]
        self.assertTrue(profile["customer_id"].startswith("CUST-"))
        self.assertEqual(profile["email"], "alert@example.com")
        self.assertEqual(profile["notification_channel"], "email")
        self.assertIn(profile, store.PREFERENCE_PROFILES)

    async def test_save_alert_respects_supplied_customer_id(self):
        response = await self.client.post(f"{self.PREFIX}/alerts", json={
            "customer_id": "CUST-EXPLICIT",
            "email": "x@y.z",
        })
        profile = (await response.get_json())["profile"]
        self.assertEqual(profile["customer_id"], "CUST-EXPLICIT")

    async def test_get_alert_profile_by_customer_id(self):
        # First save a profile with a known id.
        await self.client.post(f"{self.PREFIX}/alerts", json={
            "customer_id": "CUST-LOOKUP",
            "email": "lookup@example.com",
        })
        response = await self.client.get(f"{self.PREFIX}/alerts/CUST-LOOKUP")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(body["customer_id"], "CUST-LOOKUP")

    async def test_get_alert_profile_by_email_also_works(self):
        # Endpoint accepts either customer_id or email via case-insensitive match.
        await self.client.post(f"{self.PREFIX}/alerts", json={
            "customer_id": "CUST-BY-EMAIL",
            "email": "byemail@example.com",
        })
        response = await self.client.get(f"{self.PREFIX}/alerts/byemail@example.com")
        self.assertEqual(response.status_code, 200)

    async def test_get_alert_profile_not_found(self):
        response = await self.client.get(f"{self.PREFIX}/alerts/CUST-DOES-NOT-EXIST")
        self.assertEqual(response.status_code, 404)


# =========================================================================
# Post-booking concierge domain
# =========================================================================


class BookingsApiTests(_TravelAgencyTestBase):
    PREFIX = f"{BASE_PREFIX}/bookings"

    async def test_search_requires_email(self):
        response = await self.client.get(f"{self.PREFIX}/search")
        self.assertEqual(response.status_code, 400)
        body = await response.get_json()
        self.assertEqual(body["error"], "email_required")

    async def test_search_by_email_finds_seeded_booking(self):
        response = await self.client.get(f"{self.PREFIX}/search?email=nabil@example.com")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertGreater(body["count"], 0)

    async def test_visa_requirements_happy_path(self):
        response = await self.client.get(
            f"{self.PREFIX}/visa?nationality=New%20Zealand&destination_country=Japan"
        )
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(body["destination_country"], "Japan")
        self.assertFalse(body["visa_required"])

    async def test_visa_requirements_defaults_to_nz_when_no_nationality(self):
        response = await self.client.get(f"{self.PREFIX}/visa?destination_country=Japan")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(body["nationality"], "New Zealand")

    async def test_visa_requirements_not_found(self):
        response = await self.client.get(
            f"{self.PREFIX}/visa?nationality=Martian&destination_country=Mars"
        )
        self.assertEqual(response.status_code, 404)

    async def test_baggage_policy_happy_path(self):
        response = await self.client.get(
            f"{self.PREFIX}/baggage?airline=Air%20New%20Zealand&cabin_class=economy"
        )
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(body["checked_kg"], 23)

    async def test_baggage_policy_not_found(self):
        response = await self.client.get(f"{self.PREFIX}/baggage?airline=SkyGoat")
        self.assertEqual(response.status_code, 404)

    async def test_destination_tips_happy_path(self):
        response = await self.client.get(
            f"{self.PREFIX}/destination-tips?destination=Japan&travel_month=September"
        )
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(body["destination"], "Japan")

    async def test_destination_tips_not_found(self):
        response = await self.client.get(f"{self.PREFIX}/destination-tips?destination=Mars")
        self.assertEqual(response.status_code, 404)

    async def test_flight_checkin_happy_path(self):
        response = await self.client.get(
            f"{self.PREFIX}/checkin?flight_number=NZ99&departure_date=2026-09-10"
        )
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(body["flight_number"], "NZ99")

    async def test_flight_checkin_not_found(self):
        response = await self.client.get(f"{self.PREFIX}/checkin?flight_number=ZZ999")
        self.assertEqual(response.status_code, 404)

    async def test_insurance_options_returns_all_tiers(self):
        response = await self.client.get(f"{self.PREFIX}/insurance-options")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        tiers = {t["tier"] for t in body["tiers"]}
        self.assertEqual(tiers, {"Essentials", "Comprehensive", "Premium"})

    async def test_get_booking_by_reference_requires_last_name(self):
        # Missing last_name => 403 identity_verification_required.
        response = await self.client.get(f"{self.PREFIX}/HOT-847291")
        self.assertEqual(response.status_code, 403)
        body = await response.get_json()
        self.assertEqual(body["error"], "identity_verification_required")

    async def test_get_booking_by_reference_wrong_last_name(self):
        response = await self.client.get(f"{self.PREFIX}/HOT-847291?last_name=Nobody")
        self.assertEqual(response.status_code, 403)

    async def test_get_booking_by_reference_happy_path_is_case_insensitive(self):
        # Lowercase ref + last name match.
        response = await self.client.get(f"{self.PREFIX}/hot-847291?last_name=siddiqui")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(body["booking_reference"], "HOT-847291")

    async def test_get_booking_by_reference_not_found(self):
        response = await self.client.get(f"{self.PREFIX}/HOT-000000?last_name=Anyone")
        self.assertEqual(response.status_code, 404)

    async def test_add_insurance_updates_total(self):
        response = await self.client.post(
            f"{self.PREFIX}/HOT-847291/insurance",
            json={"insurance_tier": "Comprehensive"},
        )
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        booking = body["booking"]
        self.assertEqual(booking["insurance"], "Comprehensive")
        # Original total 1388.0 + 149 * 1 passenger = 1537.0
        self.assertEqual(booking["total_nzd"], 1537.0)

    async def test_add_insurance_unknown_tier(self):
        response = await self.client.post(
            f"{self.PREFIX}/HOT-847291/insurance",
            json={"insurance_tier": "Ultra"},
        )
        self.assertEqual(response.status_code, 404)
        body = await response.get_json()
        self.assertEqual(body["error"], "insurance_tier_not_found")

    async def test_add_insurance_unknown_booking(self):
        response = await self.client.post(
            f"{self.PREFIX}/HOT-000000/insurance",
            json={"insurance_tier": "Essentials"},
        )
        self.assertEqual(response.status_code, 404)


# =========================================================================
# Consultant match domain
# =========================================================================


class ConsultantsApiTests(_TravelAgencyTestBase):
    PREFIX = f"{BASE_PREFIX}/consultants"

    async def test_search_filters_by_city(self):
        response = await self.client.get(f"{self.PREFIX}/search?city=Christchurch")
        body = await response.get_json()
        self.assertGreater(body["count"], 0)
        for consultant in body["consultants"]:
            self.assertEqual(consultant["city"], "Christchurch")

    async def test_search_filters_by_specialisation(self):
        response = await self.client.get(f"{self.PREFIX}/search?travel_type=Cruise")
        body = await response.get_json()
        for consultant in body["consultants"]:
            self.assertTrue(
                any("cruise" in s.lower() for s in consultant["specialisations"])
            )

    async def test_search_results_sorted_by_rating_descending(self):
        response = await self.client.get(f"{self.PREFIX}/search")
        body = await response.get_json()
        ratings = [c["rating"] for c in body["consultants"]]
        self.assertEqual(ratings, sorted(ratings, reverse=True))

    async def test_find_stores_filters_by_city_or_suburb(self):
        response = await self.client.get(f"{self.PREFIX}/stores?city=Christchurch")
        body = await response.get_json()
        self.assertGreater(body["count"], 0)

    async def test_store_hours_happy_path(self):
        response = await self.client.get(f"{self.PREFIX}/stores/STORE-CHC-CITY/hours")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(body["store_id"], "STORE-CHC-CITY")
        self.assertIn("Mon", body["hours"])

    async def test_store_hours_not_found(self):
        response = await self.client.get(f"{self.PREFIX}/stores/STORE-NOPE/hours")
        self.assertEqual(response.status_code, 404)

    async def test_consultant_profile_happy_path(self):
        response = await self.client.get(f"{self.PREFIX}/CONS-CHC-001")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(body["consultant_id"], "CONS-CHC-001")

    async def test_consultant_profile_not_found(self):
        response = await self.client.get(f"{self.PREFIX}/CONS-NOPE")
        self.assertEqual(response.status_code, 404)

    async def test_book_appointment_returns_reference_and_video_url(self):
        response = await self.client.post(
            f"{self.PREFIX}/CONS-CHC-001/appointment",
            json={"appointment_type": "video", "preferred_datetime": "2026-08-01T10:00"},
        )
        self.assertEqual(response.status_code, 201)
        body = await response.get_json()
        self.assertTrue(body["booked"])
        self.assertTrue(body["appointment_reference"].startswith("APPT-"))
        self.assertEqual(body["consultant_id"], "CONS-CHC-001")
        self.assertIn("outlook.office.com", body["video_appointment_url"])

    async def test_book_appointment_unknown_consultant(self):
        response = await self.client.post(f"{self.PREFIX}/CONS-NOPE/appointment", json={})
        self.assertEqual(response.status_code, 404)

    async def test_request_callback_returns_reference(self):
        response = await self.client.post(
            f"{self.PREFIX}/CONS-CHC-001/callback",
            json={"customer_phone": "+64211234567", "urgency": "high"},
        )
        self.assertEqual(response.status_code, 201)
        body = await response.get_json()
        self.assertTrue(body["callback_queued"])
        self.assertTrue(body["callback_reference"].startswith("CB-"))
        self.assertEqual(body["urgency"], "high")

    async def test_request_callback_unknown_consultant(self):
        response = await self.client.post(f"{self.PREFIX}/CONS-NOPE/callback", json={})
        self.assertEqual(response.status_code, 404)


# =========================================================================
# General information domain
# =========================================================================


class GeneralApiTests(_TravelAgencyTestBase):
    PREFIX = f"{BASE_PREFIX}/general"

    async def test_store_info_by_city(self):
        response = await self.client.get(f"{self.PREFIX}/stores/Christchurch")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertGreater(body["count"], 0)

    async def test_faq_without_query_returns_all(self):
        response = await self.client.get(f"{self.PREFIX}/faqs")
        body = await response.get_json()
        self.assertEqual(body["count"], len(store.FAQS))

    async def test_faq_keyword_match(self):
        # First FAQ has 'payment' in keywords.
        response = await self.client.get(f"{self.PREFIX}/faqs?q=payment")
        body = await response.get_json()
        self.assertGreater(body["count"], 0)

    async def test_faq_no_match_returns_empty(self):
        response = await self.client.get(f"{self.PREFIX}/faqs?q=xyzzy-nomatch")
        body = await response.get_json()
        self.assertEqual(body["count"], 0)

    async def test_payment_options_shape(self):
        response = await self.client.get(f"{self.PREFIX}/payment-options")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertIn("methods", body)
        self.assertIn("q_card_terms", body)

    async def test_gift_cards_shape(self):
        response = await self.client.get(f"{self.PREFIX}/gift-cards")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertIn("denominations_nzd", body)

    async def test_app_info_shape(self):
        response = await self.client.get(f"{self.PREFIX}/app-info")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertIn("features", body)
        self.assertIn("download_links", body)

    async def test_insurance_overview(self):
        response = await self.client.get(f"{self.PREFIX}/insurance-overview")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertIn("overview", body)
        self.assertIsInstance(body["overview"], str)

    async def test_travel_advisory_happy_path(self):
        response = await self.client.get(f"{self.PREFIX}/travel-advisory?destination=Japan")
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertEqual(body["destination"], "Japan")

    async def test_travel_advisory_not_found(self):
        response = await self.client.get(f"{self.PREFIX}/travel-advisory?destination=Mars")
        self.assertEqual(response.status_code, 404)


# =========================================================================
# Support domain
# =========================================================================


class SupportApiTests(_TravelAgencyTestBase):
    PREFIX = f"{BASE_PREFIX}/support"

    async def test_escalate_returns_reference(self):
        response = await self.client.post(f"{self.PREFIX}/escalate", json={
            "session_id": "SESS-1", "reason": "billing dispute", "urgency": "high",
        })
        self.assertEqual(response.status_code, 201)
        body = await response.get_json()
        self.assertTrue(body["escalated"])
        self.assertTrue(body["escalation_reference"].startswith("ESC-"))
        self.assertEqual(body["urgency"], "high")

    async def test_escalate_defaults_to_normal_urgency(self):
        response = await self.client.post(f"{self.PREFIX}/escalate", json={})
        body = await response.get_json()
        self.assertEqual(body["urgency"], "normal")

    async def test_route_recognises_known_intent(self):
        response = await self.client.post(f"{self.PREFIX}/route", json={
            "intent": "FLIGHT_BOOKING", "session_id": "S1",
        })
        self.assertEqual(response.status_code, 200)
        body = await response.get_json()
        self.assertTrue(body["routed"])
        self.assertTrue(body["recognised"])

    async def test_route_flags_unknown_intent(self):
        response = await self.client.post(f"{self.PREFIX}/route", json={
            "intent": "MADE_UP",
        })
        body = await response.get_json()
        self.assertTrue(body["routed"])
        self.assertFalse(body["recognised"])


# =========================================================================
# Cross-cutting: store isolation between tests
# =========================================================================


class StoreIsolationTests(_TravelAgencyTestBase):
    """Guard that ``asyncTearDown`` actually restores mutable seed collections.
    Without this, POST tests in one file could accumulate rows visible to
    unrelated tests, breaking counts and matches non-deterministically."""

    async def test_bookings_are_snapshotted_before_test(self):
        # Snapshot equals current store contents at asyncSetUp time.
        self.assertEqual(self._bookings_snapshot, store.BOOKINGS)

    async def test_added_booking_is_rolled_back_after_test(self):
        # Simulate what a POST would do; asyncTearDown should undo it.
        marker = {"booking_reference": "HOT-ISOLATION-MARKER"}
        store.BOOKINGS.append(marker)
        self.assertIn(marker, store.BOOKINGS)
        # After this test's asyncTearDown runs, the marker will be gone;
        # subsequent tests should not see it (verified by the sibling test).

    async def test_marker_from_previous_test_is_absent(self):
        # Because IsolatedAsyncioTestCase runs tests in method-name order,
        # this runs after `test_added_booking_is_rolled_back_after_test`.
        refs = {b.get("booking_reference") for b in store.BOOKINGS}
        self.assertNotIn("HOT-ISOLATION-MARKER", refs)


if __name__ == "__main__":
    unittest.main()
