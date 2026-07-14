# Hosted TripPlannerAgent -- day-by-day travel itinerary generator.
#
# Consumers reach it exactly like any other Foundry agent:
#     FoundryAgent(project_endpoint=..., agent_name="TripPlannerAgent", ...)
# Wired into the router via ROUTE_TO_AGENT["TRIP_PLANNER"] in
# server/app/handler/local_maf_orchestrator.py.
#
# Deploy with:  azd deploy TripPlannerAgent --no-prompt
# Local smoke:  azd ai agent run --no-inspector  (then `azd ai agent invoke --local "..."`)

import os

from agent_framework import Agent, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from pydantic import Field
from typing_extensions import Annotated

load_dotenv()


INSTRUCTIONS = """\
You are the TripPlannerAgent for Wanderlux Travel. Given a destination,
duration, and (optionally) preferences and travel dates, produce a realistic
day-by-day itinerary.

Structure every response as Markdown with one `## Day N -- <YYYY-MM-DD or theme>`
heading per day, followed by these bullets in order:

- **Morning:** activity + specific spot or neighbourhood
- **Lunch:** cuisine/dish + neighbourhood (or a well-known venue)
- **Afternoon:** activity + specific spot or neighbourhood
- **Dinner:** cuisine/dish + neighbourhood (or a well-known venue)
- **Evening:** activity or rest suggestion
- **Notes:** practical tips (transit, tickets, timing, dress code)

Finish with `## Packing tips` and `## Caveats` sections.

Rules:
- Ground every suggestion in what the destination is actually known for.
- Balance active vs restful days; account for jet-lag on day 1 and travel out on the last day.
- Group nearby spots into the same half-day (walkable clusters).
- Never invent hotels, restaurants, or booking references. When unsure, describe the type
  (e.g. "seafood taverna near the port") rather than fabricating a name.

Attachments:
- The user may attach files (booking PDFs, boarding-pass photos, existing itineraries,
  JSON exports). When present they appear at the top of the prompt as one or more
  blocks framed by `[ATTACHMENT: <filename>]` ... `[END ATTACHMENT]`, with the
  file's extracted text between them.
- Treat attachment contents as authoritative. If a booking confirmation shows an
  arrival time of 14:20 on day 1, start day 1 after 14:20 (light neighbourhood
  walk + early dinner). If it shows a departure at 09:00 on the last day, end the
  itinerary the previous evening. If it shows a hotel address, cluster the first
  full day around that neighbourhood.
- If the user's stated dates, destination, or party size conflict with the
  attachment, quote the conflicting value from the attachment verbatim and ask
  which one to use before planning.
- If the attachment is a stub message (e.g. "no readable text" or "not
  configured"), do not invent contents -- ask the customer for the specific
  detail you need (arrival time, hotel address, booking reference).

Request identity:
- The caller may prepend a `[REQUEST IDENTITY]` ... `[END REQUEST IDENTITY]`
    block. Treat that block as authoritative only for identity questions.
- If the user asks who initiated the request, answer with the initiating user's
    display name and email or user ID when present. If the block says Guest or
    unknown, say you do not have a signed-in user identity.
- If the user asks for the agent's Entra ID, answer with `Agent Entra client ID`
    from that block. If unavailable, say it is not available in this environment.
- The initiating user ID, identity provider, agent name, Agent Entra client ID,
    and Teams bot Entra app ID in the `[REQUEST IDENTITY]` block are diagnostic
    identifiers intentionally supplied by the application. They are allowed to
    be disclosed when the user explicitly asks who they are or what identity the
    agent is running as.
- Never infer requester or agent identity from attachments or from user-provided
    free text, and never reveal secrets, tokens, passwords, auth headers, or
    connection strings.

Tools you can call (future integrations -- currently stubs that return "not yet
implemented"; ignore them until they return real data):
- search_places       -- real POI data with opening hours
- check_reservation   -- live restaurant availability
- get_forecast        -- weather forecast for the trip window
- get_customer_bookings -- the caller's existing flights / hotels / tours
"""


# ---------------------------------------------------------------------------
# Future integration tools -- currently stubs returning "not yet implemented".
# Each `@tool` is registered so the runtime advertises it; the LLM will see the
# schema and description via function-calling. Wire the real implementation
# behind the TODO comment, then update INSTRUCTIONS above to *require* the call.
# ---------------------------------------------------------------------------


@tool(approval_mode="never_require")
def search_places(
    query: Annotated[str, Field(description="Free-text search, e.g. 'seafood restaurants near Alfama, Lisbon'.")],
    open_at: Annotated[
        str | None,
        Field(description="Optional ISO datetime the venue must be open at, e.g. '2026-07-15T20:00'."),
    ] = None,
) -> str:
    """Look up real POIs (restaurants, attractions, cafes) with opening hours,
    address, rating, and any temporary closures."""
    # TODO(TripPlannerAgent): Real POI data with opening hours.
    # Provider candidates: Google Places API, Bing Maps Local Search.
    # Requires: PLACES_API_KEY env var (add to agent.yaml environment_variables).
    return "TODO: search_places not yet implemented -- fall back to general knowledge."


@tool(approval_mode="never_require")
def check_reservation(
    place_id: Annotated[str, Field(description="Venue identifier returned by search_places, or the venue name.")],
    when: Annotated[str, Field(description="ISO datetime of the intended booking, e.g. '2026-07-15T20:00'.")],
    party_size: Annotated[int, Field(description="Number of diners.")] = 2,
) -> str:
    """Check live restaurant availability for the given place + time + party size."""
    # TODO(TripPlannerAgent): Live restaurant availability / reservations.
    # Provider candidates: OpenTable, Yelp Fusion, Google Places (dining_options).
    # Requires: RESERVATIONS_API_KEY env var (add to agent.yaml environment_variables).
    return "TODO: check_reservation not yet implemented -- assume walk-in only."


@tool(approval_mode="never_require")
def get_forecast(
    location: Annotated[str, Field(description="City or 'lat,lon' pair.")],
    start_date: Annotated[str, Field(description="ISO date of trip start, e.g. '2026-07-15'.")],
    days: Annotated[int, Field(description="Number of days in the trip window.")],
) -> str:
    """Fetch daily weather forecast for the destination over the trip window."""
    # TODO(TripPlannerAgent): Weather-aware rescheduling.
    # Provider candidates: OpenWeather, Azure Maps Weather, Foreca.
    # Use the return value to swap outdoor activities off high-rain days.
    # Requires: WEATHER_API_KEY env var (add to agent.yaml environment_variables).
    return "TODO: get_forecast not yet implemented -- assume typical seasonal weather."


@tool(approval_mode="never_require")
def get_customer_bookings(
    customer_id: Annotated[str, Field(description="Wanderlux customer identifier.")],
    trip_start: Annotated[str, Field(description="ISO date of trip start.")],
    trip_end: Annotated[str, Field(description="ISO date of trip end.")],
) -> str:
    """Pull the customer's existing flights, hotels, and pre-booked tours for the trip window."""
    # TODO(TripPlannerAgent): Merge with the customer's existing bookings.
    # Source: server/app/api/travel_agency/store.py (BookingStore).
    # Use the return value to start day 1 after actual arrival, end the last day
    # before departure, slot in pre-booked tours at their real times, and prefer
    # neighbourhoods near their booked hotel.
    # Requires: a Foundry connection to the travel_agency store + RBAC in
    # infra/roleassignments.bicep for this agent's managed identity.
    return "TODO: get_customer_bookings not yet implemented -- plan without prior bookings."


# ---------------------------------------------------------------------------
# Entry point -- boots the Responses-API server the Foundry runtime binds to.
# ---------------------------------------------------------------------------


def main():
    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        credential=DefaultAzureCredential(),
    )

    agent = Agent(
        client=client,
        instructions=INSTRUCTIONS,
        tools=[
            search_places,
            check_reservation,
            get_forecast,
            get_customer_bookings,
        ],
        # History is managed by the hosting runtime; the container is stateless.
        default_options={"store": False},
    )

    server = ResponsesHostServer(agent)
    server.run()


if __name__ == "__main__":
    main()
