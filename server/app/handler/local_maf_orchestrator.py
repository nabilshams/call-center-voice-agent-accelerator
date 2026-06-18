"""Local Microsoft Agent Framework travel orchestrator.

Primary path uses Native MAF SDK agents (FoundryChatClient + as_agent).
Fallback path preserves deterministic branch routing when SDK/config is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
import logging
import re
from typing import Any

from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentDecision:
    route: str
    confidence: float
    rationale: str


@dataclass(frozen=True)
class AgentResult:
    agent: str
    summary: str
    missing_fields: list[str]
    confidence: float


class LocalMAFTravelOrchestrator:
    """Local orchestrator that prefers Native MAF SDK with deterministic fallback."""

    ROUTE_TO_AGENT = {
        "FLIGHT_BOOKING": "FlightBookingAgent",
        "HOLIDAY_PACKAGE": "HolidayPackageAgent",
        "CRUISE": "CruiseDiscoveryAgent",
        "TOUR": "TourMatchingAgent",
        "INSPIRATION": "TravelInspirationAgent",
        "POST_BOOKING": "PostBookingConcierge",
        "CONSULTANT": "ConsultantMatchAgent",
        "DEAL_ALERT": "DealAlertAgent",
    }

    ROUTE_KEYWORDS = {
        "FLIGHT_BOOKING": ["flight", "airline", "depart", "return", "layover", "cabin"],
        "HOLIDAY_PACKAGE": ["package", "bundle", "all-inclusive", "all inclusive"],
        "CRUISE": ["cruise", "ship", "deck", "port"],
        "TOUR": ["tour", "guide", "excursion", "activity", "day trip"],
        "INSPIRATION": ["inspiration", "ideas", "where should", "recommend destination"],
        "POST_BOOKING": ["booking", "ticket", "confirmation", "change", "cancel", "check-in", "baggage"],
        "CONSULTANT": ["consultant", "advisor", "human", "specialist", "agent"],
        "DEAL_ALERT": ["deal", "discount", "price drop", "alert", "offer"],
    }

    AGENT_REQUIRED_FIELDS = {
        "FlightBookingAgent": ["origin", "destination", "departure_date", "travelers"],
        "HolidayPackageAgent": ["destination", "date_range", "budget"],
        "CruiseDiscoveryAgent": ["region", "date_range", "travelers"],
        "TourMatchingAgent": ["destination", "travel_dates", "interests"],
        "TravelInspirationAgent": ["season", "budget", "trip_style"],
        "PostBookingConcierge": ["booking_reference", "support_topic"],
        "ConsultantMatchAgent": ["trip_type", "budget", "timeline"],
        "DealAlertAgent": ["route_or_destination", "date_window", "budget"],
    }

    # Every field name the extractor may emit (union of all agent requirements).
    ALL_FIELDS = (
        "origin", "destination", "departure_date", "travelers", "date_range",
        "budget", "region", "travel_dates", "interests", "season", "trip_style",
        "booking_reference", "support_topic", "trip_type", "timeline",
        "route_or_destination", "date_window",
    )

    AGENT_SUMMARIES = {
        "FlightBookingAgent": "I can shortlist flight options once origin, destination, dates, and traveler count are confirmed.",
        "HolidayPackageAgent": "I can compare package options based on destination, dates, and your budget range.",
        "CruiseDiscoveryAgent": "I can recommend cruise options by region, season, and traveler profile.",
        "TourMatchingAgent": "I can suggest tours that match your destination, dates, and interests.",
        "TravelInspirationAgent": "I can suggest destinations aligned with your budget, season, and travel style.",
        "PostBookingConcierge": "I can help with booking changes, check-in guidance, and post-booking support.",
        "ConsultantMatchAgent": "I can match you with a specialist consultant for your trip type and timeline.",
        "DealAlertAgent": "I can track deals for your route and preferred date window once those details are set.",
    }

    def __init__(self, config: dict[str, Any]):
        self._config = config
        self._native_enabled = str(config.get("MAF_NATIVE_SDK_ENABLED", "true")).lower() == "true"
        self._project_endpoint = (
            config.get("MAF_PROJECT_ENDPOINT")
            or config.get("FOUNDRY_PROJECT_ENDPOINT")
            or ""
        )
        self._model = config.get("MAF_MODEL") or config.get("AZURE_OPENAI_CHAT_DEPLOYMENT") or ""
        self._managed_identity_client_id = (
            config.get("AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID")
            or config.get("AZURE_CLIENT_ID")
            or ""
        )

        self._foundry_client = None
        self._agent_cache: dict[str, Any] = {}
        self._native_ready = False
        self._native_init_error = ""

        self._initialize_native_sdk()

    def _initialize_native_sdk(self):
        if not self._native_enabled:
            self._native_init_error = "MAF native SDK disabled by configuration"
            return

        if not self._project_endpoint or not self._model:
            self._native_init_error = "Missing MAF project endpoint or model"
            return

        try:
            from agent_framework.foundry import FoundryChatClient  # type: ignore

            self._foundry_client = FoundryChatClient(
                project_endpoint=self._project_endpoint,
                model=self._model,
                credential=DefaultAzureCredential(
                    managed_identity_client_id=self._managed_identity_client_id or None
                ),
            )
            self._native_ready = True
        except (ImportError, RuntimeError, ValueError, TypeError, AttributeError) as exc:
            self._native_init_error = str(exc)
            logger.warning("Native MAF SDK unavailable, using deterministic fallback: %s", exc)

    async def orchestrate(self, message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        if not message or not message.strip():
            return {
                "spoken_reply": "Please tell me what you want help with for your trip.",
                "clarification_question": "Would you like help with flights, holiday packages, cruises, tours, or post-booking support?",
                "selected_agents": [],
                "specialist_outputs": [],
                "confidence": 0.2,
                "next_step": "collect_intent",
            }

        ctx = context or {}

        decision = await self._orchestrator_agent(message=message, context=ctx)
        selected_agent = self.ROUTE_TO_AGENT.get(decision.route, "ConsultantMatchAgent")
        extracted_fields = await self._extract_fields(message, ctx)
        specialist_result = await self._run_specialist_agent(
            selected_agent, message=message, context=ctx, extracted_fields=extracted_fields
        )

        workflow_trace = [
            {
                "node": "OrchestratorAgent",
                "route": decision.route,
                "confidence": decision.confidence,
                "rationale": decision.rationale,
            },
            {
                "node": selected_agent,
                "confidence": specialist_result.confidence,
            },
        ]

        if specialist_result.missing_fields:
            # Prefer the Foundry-generated specialist summary when native is active;
            # fall back to the generic prompt only when running deterministically.
            spoken = (
                specialist_result.summary
                if self._native_ready
                else "I can help with that. I just need a few details first."
            )
            return {
                "spoken_reply": spoken,
                "clarification_question": self._build_question(specialist_result.missing_fields),
                "selected_agents": [selected_agent],
                "specialist_outputs": [
                    {
                        "agent": specialist_result.agent,
                        "request": message,
                        "summary": specialist_result.summary,
                        "missing_fields": specialist_result.missing_fields,
                        "confidence": specialist_result.confidence,
                    }
                ],
                "confidence": round((decision.confidence + specialist_result.confidence) / 2, 2),
                "next_step": "collect_required_fields",
                "workflow_route": decision.route,
                "workflow_trace": workflow_trace,
                "maf_runtime": "native" if self._native_ready else "deterministic-fallback",
                "maf_init_error": "" if self._native_ready else self._native_init_error,
            }

        return {
            "spoken_reply": specialist_result.summary,
            "clarification_question": None,
            "selected_agents": [selected_agent],
            "specialist_outputs": [
                {
                    "agent": specialist_result.agent,
                    "request": message,
                    "summary": specialist_result.summary,
                    "missing_fields": [],
                    "confidence": specialist_result.confidence,
                }
            ],
            "confidence": round((decision.confidence + specialist_result.confidence) / 2, 2),
            "next_step": "present_options",
            "workflow_route": decision.route,
            "workflow_trace": workflow_trace,
            "maf_runtime": "native" if self._native_ready else "deterministic-fallback",
            "maf_init_error": "" if self._native_ready else self._native_init_error,
        }

    async def orchestrate_multi(self, message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Multi-Intent orchestration.

        Detects one or more intents in a single request, fans the work out to the
        matching specialist agents in parallel, waits for every response, then
        aggregates them into one combined reply.
        """
        if not message or not message.strip():
            return {
                "spoken_reply": "Please tell me what you want help with for your trip.",
                "clarification_question": "Would you like help with flights, holiday packages, cruises, tours, or post-booking support?",
                "selected_agents": [],
                "specialist_outputs": [],
                "confidence": 0.2,
                "next_step": "collect_intent",
                "orchestration_strategy": "multi-intent",
            }

        ctx = context or {}

        routes = await self._detect_multi_routes(message=message, context=ctx)
        selected_agents: list[str] = []
        for route in routes:
            agent = self.ROUTE_TO_AGENT.get(route)
            if agent and agent not in selected_agents:
                selected_agents.append(agent)
        if not selected_agents:
            selected_agents = ["ConsultantMatchAgent"]

        # Extract fields once for the whole request, then reuse across every agent.
        extracted_fields = await self._extract_fields(message, ctx)

        # Fan out to every selected specialist concurrently and wait for all of them.
        results = await asyncio.gather(
            *[
                self._run_specialist_agent(
                    agent, message=message, context=ctx, extracted_fields=extracted_fields
                )
                for agent in selected_agents
            ]
        )

        specialist_outputs: list[dict[str, Any]] = []
        aggregated_missing: list[str] = []
        confidences: list[float] = []
        workflow_trace: list[dict[str, Any]] = [
            {
                "node": "MultiIntentOrchestrator",
                "routes": routes,
                "parallel_agents": len(selected_agents),
            }
        ]

        for result in results:
            specialist_outputs.append(
                {
                    "agent": result.agent,
                    "request": message,
                    "summary": result.summary,
                    "missing_fields": result.missing_fields,
                    "confidence": result.confidence,
                }
            )
            confidences.append(result.confidence)
            for field in result.missing_fields:
                if field not in aggregated_missing:
                    aggregated_missing.append(field)
            workflow_trace.append({"node": result.agent, "confidence": result.confidence})

        combined_reply = self._combine_specialist_summaries(results)
        clarification = self._build_question(aggregated_missing) if aggregated_missing else None
        avg_confidence = round(sum(confidences) / len(confidences), 2) if confidences else 0.5

        return {
            "spoken_reply": combined_reply,
            "clarification_question": clarification,
            "selected_agents": selected_agents,
            "specialist_outputs": specialist_outputs,
            "confidence": avg_confidence,
            "next_step": "collect_required_fields" if aggregated_missing else "present_options",
            "workflow_route": "+".join(routes),
            "workflow_trace": workflow_trace,
            "orchestration_strategy": "multi-intent",
            "maf_runtime": "native" if self._native_ready else "deterministic-fallback",
            "maf_init_error": "" if self._native_ready else self._native_init_error,
        }

    async def _detect_multi_routes(self, message: str, context: dict[str, Any]) -> list[str]:
        """Detect one or more intent routes for parallel orchestration."""
        if self._native_ready:
            routes = await self._run_native_multi_router(message, context)
            if routes:
                return routes

        if isinstance(context.get("route_hint"), str):
            hint = context["route_hint"].strip().upper()
            if hint in self.ROUTE_TO_AGENT:
                return [hint]

        text = message.lower()
        scores: list[tuple[str, int]] = []
        for route, keywords in self.ROUTE_KEYWORDS.items():
            score = sum(1 for term in keywords if term in text)
            if score > 0:
                scores.append((route, score))

        if not scores:
            return ["CONSULTANT"]

        scores.sort(key=lambda item: item[1], reverse=True)
        return [route for route, _ in scores[:3]]

    async def _run_native_multi_router(self, message: str, context: dict[str, Any]) -> list[str]:
        instructions = (
            "You are MultiIntentOrchestrator. "
            "A single user request may contain multiple travel intents. "
            "Return every applicable route token as a comma-separated list and no other text. "
            "Valid tokens: FLIGHT_BOOKING, HOLIDAY_PACKAGE, CRUISE, TOUR, INSPIRATION, "
            "POST_BOOKING, CONSULTANT, DEAL_ALERT. "
            "Return between 1 and 3 tokens, most relevant first."
        )
        prompt = (
            "Identify all travel intents in this request.\n"
            f"Message: {message}\n"
            f"Context: {context}\n"
            "Answer with comma-separated tokens only."
        )
        text = await self._run_native_agent_once(
            agent_name="MultiIntentOrchestrator",
            instructions=instructions,
            prompt=prompt,
        )
        if not text:
            return []

        upper = text.strip().upper()
        found: list[str] = []
        for token in self.ROUTE_TO_AGENT:
            if re.search(rf"\b{token}\b", upper) and token not in found:
                found.append(token)
        return found[:3]

    def _combine_specialist_summaries(self, results: list[AgentResult]) -> str:
        """Merge multiple specialist summaries into one cohesive voice-safe reply."""
        valid = [result for result in results if result.summary]
        if not valid:
            return "I can help with travel planning. Tell me what you need."
        if len(valid) == 1:
            return valid[0].summary

        lead = "Here's how I can help across the areas you mentioned. "
        body = " ".join(result.summary for result in valid)
        return lead + body

    async def _orchestrator_agent(self, message: str, context: dict[str, Any]) -> AgentDecision:
        if self._native_ready:
            route = await self._run_native_router(message, context)
            if route in self.ROUTE_TO_AGENT:
                return AgentDecision(
                    route=route,
                    confidence=0.9,
                    rationale="Route selected by Native MAF OrchestratorAgent.",
                )

        # Only use explicit route hints when native orchestrator routing is unavailable.
        if isinstance(context.get("route_hint"), str):
            hint = context["route_hint"].strip().upper()
            if hint in self.ROUTE_TO_AGENT:
                return AgentDecision(
                    route=hint,
                    confidence=0.97,
                    rationale="Route selected from explicit context.route_hint.",
                )

        text = message.lower()
        scores: list[tuple[str, int]] = []
        for route, keywords in self.ROUTE_KEYWORDS.items():
            score = sum(1 for term in keywords if term in text)
            if score > 0:
                scores.append((route, score))

        if not scores:
            return AgentDecision(
                route="CONSULTANT",
                confidence=0.45,
                rationale="No strong route signal, defaulting to ConsultantMatchAgent.",
            )

        scores.sort(key=lambda item: item[1], reverse=True)
        top_route, top_score = scores[0]
        confidence = min(0.98, 0.55 + (top_score * 0.12))
        return AgentDecision(
            route=top_route,
            confidence=round(confidence, 2),
            rationale=f"Top keyword route match for {top_route}.",
        )

    async def _run_specialist_agent(
        self,
        agent_name: str,
        message: str,
        context: dict[str, Any],
        extracted_fields: dict[str, str] | None = None,
    ) -> AgentResult:
        required = self.AGENT_REQUIRED_FIELDS.get(agent_name, [])

        # Prefer fields already extracted for this request; otherwise extract now.
        if extracted_fields is None:
            extracted_fields = await self._extract_fields(message, context)

        missing = [field for field in required
                   if field not in extracted_fields and not self._has_value(context, field)]
        confidence = 0.86 if not missing else 0.58

        summary = self.AGENT_SUMMARIES.get(agent_name, "I can support this travel request.")

        if self._native_ready:
            # Build comprehensive prompt with conversation history
            history_text = self._format_conversation_history(context)
            extracted_summary = self._format_extracted_fields(extracted_fields)
            
            prompt = (
                "Conversation history:\n"
                f"{history_text}\n\n"
                f"Current user request: {message}\n\n"
                f"Information already collected:\n{extracted_summary}\n\n"
                f"Required fields still needed: {', '.join(missing) if missing else 'All information collected'}\n\n"
                "Instructions:\n"
                "- Do NOT ask for information that has already been collected.\n"
                "- Provide a short voice-safe summary in 1-2 sentences.\n"
                "- Do not invent prices, booking IDs, or availability.\n"
                "- If all required information is available, provide helpful guidance based on their choices."
            )
            native_summary = await self._run_native_specialist(agent_name, prompt)
            if native_summary:
                summary = native_summary
                confidence = 0.9 if not missing else 0.64

        return AgentResult(
            agent=agent_name,
            summary=summary,
            missing_fields=missing,
            confidence=confidence,
        )

    async def _run_native_router(self, message: str, context: dict[str, Any]) -> str:
        instructions = (
            "You are OrchestratorAgent. "
            "Return exactly one route token and no other text. "
            "Valid tokens: FLIGHT_BOOKING, HOLIDAY_PACKAGE, CRUISE, TOUR, INSPIRATION, "
            "POST_BOOKING, CONSULTANT, DEAL_ALERT."
        )
        prompt = (
            "Classify this request into one token.\n"
            f"Message: {message}\n"
            f"Context: {context}\n"
            "Answer with token only."
        )
        text = await self._run_native_agent_once(
            agent_name="OrchestratorAgent",
            instructions=instructions,
            prompt=prompt,
        )
        if not text:
            return ""

        upper = text.strip().upper()
        if upper in self.ROUTE_TO_AGENT:
            return upper

        for token in self.ROUTE_TO_AGENT:
            if re.search(rf"\b{token}\b", upper):
                return token

        return ""

    async def _run_native_specialist(self, agent_name: str, prompt: str) -> str:
        instructions = (
            f"You are {agent_name}. "
            "Your role is to help the user with their travel request. "
            "IMPORTANT RULES:\n"
            "1. Review the conversation history FIRST to see what information has already been provided.\n"
            "2. NEVER ask for information that was already mentioned in prior messages.\n"
            "3. Only ask for truly missing information that hasn't been discussed yet.\n"
            "4. If you notice the user already provided something, acknowledge it and move forward.\n"
            "5. Provide concise travel guidance in 1-2 sentences suitable for voice conversation.\n"
            "6. Never claim a booking is completed unless explicitly confirmed by system state.\n"
            "7. Do not invent prices, booking IDs, or availability information."
        )
        return await self._run_native_agent_once(
            agent_name=agent_name,
            instructions=instructions,
            prompt=prompt,
        )

    async def _run_native_agent_once(self, agent_name: str, instructions: str, prompt: str) -> str:
        if not self._native_ready or self._foundry_client is None:
            return ""

        try:
            agent = self._agent_cache.get(agent_name)
            if agent is None:
                agent = self._foundry_client.as_agent(
                    name=agent_name,
                    instructions=instructions,
                )
                self._agent_cache[agent_name] = agent

            result = await agent.run(prompt)
            return str(result).strip()
        except Exception as exc:
            # Native SDK may raise provider-specific errors (e.g., auth failures).
            # Record and downgrade to deterministic fallback instead of surfacing 500s.
            self._native_init_error = str(exc)
            logger.warning("Native MAF agent run failed for %s: %s", agent_name, exc)
            return ""

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
            "date_range": "travel date range",
            "budget": "budget",
            "region": "cruise region",
            "travel_dates": "travel dates",
            "interests": "interests",
            "season": "preferred season",
            "trip_style": "trip style",
            "booking_reference": "booking reference",
            "support_topic": "support topic",
            "trip_type": "trip type",
            "timeline": "timeline",
            "route_or_destination": "route or destination",
            "date_window": "date window",
        }
        readable = [labels.get(field, field.replace("_", " ")) for field in missing_fields]

        if len(readable) == 1:
            return f"Could you share your {readable[0]}?"
        if len(readable) == 2:
            return f"Could you share your {readable[0]} and {readable[1]}?"
        return f"Could you share your {', '.join(readable[:-1])}, and {readable[-1]}?"

    async def _extract_fields(self, message: str, context: dict[str, Any]) -> dict[str, str]:
        """Extract travel fields, preferring the LLM and falling back to deterministic rules."""
        if self._native_ready:
            llm_fields = await self._extract_fields_llm(message, context)
            if llm_fields:
                return llm_fields
        # SDK unavailable or the model returned nothing usable.
        return self._extract_fields_from_context(context)

    async def _extract_fields_llm(self, message: str, context: dict[str, Any]) -> dict[str, str]:
        """Use the model to read the conversation and return structured travel fields."""
        history_text = self._format_conversation_history(context)
        instructions = (
            "You are a travel information extraction engine. "
            "Read the conversation and extract only the booking details the traveler "
            "has actually provided. "
            "Respond with a single compact JSON object mapping field names to short "
            "string values, and nothing else (no prose, no code fences). "
            "Allowed field names: " + ", ".join(self.ALL_FIELDS) + ". "
            "Only include a field when the value is clearly stated or strongly implied; "
            "omit anything unknown and never guess. "
            'Example: {"destination": "Maldives", "date_range": "late September", '
            '"budget": "12000 USD", "travelers": "2"}'
        )
        prompt = (
            "Conversation so far:\n"
            f"{history_text}\n\n"
            f"Latest traveler message: {message}\n\n"
            "Return the JSON object of extracted fields now."
        )
        raw = await self._run_native_agent_once(
            agent_name="FieldExtractionAgent",
            instructions=instructions,
            prompt=prompt,
        )
        if not raw:
            return {}
        return self._parse_field_json(raw)

    def _parse_field_json(self, raw: str) -> dict[str, str]:
        """Parse the model's JSON reply into a clean {field: value} dict."""
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text).strip()

        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            logger.warning("Field extraction returned non-JSON output: %s", raw[:200])
            return {}
        if not isinstance(data, dict):
            return {}

        result: dict[str, str] = {}
        for key, value in data.items():
            if key in self.ALL_FIELDS and isinstance(value, (str, int, float)):
                cleaned = str(value).strip()
                if cleaned:
                    result[key] = cleaned

        # Keep the date_range / travel_dates pair consistent for downstream agents.
        if "date_range" in result and "travel_dates" not in result:
            result["travel_dates"] = result["date_range"]
        if "travel_dates" in result and "date_range" not in result:
            result["date_range"] = result["travel_dates"]
        return result

    def _extract_fields_from_context(self, context: dict[str, Any]) -> dict[str, str]:
        """Deterministic fallback extraction used when the model is unavailable."""
        extracted = {}

        # Direct context fields
        for field in ["origin", "destination", "date_range", "travel_dates", "budget",
                      "region", "interests", "booking_reference", "support_topic"]:
            if self._has_value(context, field):
                extracted[field] = str(context.get(field))

        # Extract from conversation history by looking for keywords
        history = context.get("history", [])
        if isinstance(history, list):
            raw_text = " ".join(str(turn.get("text", "")) for turn in history
                                if isinstance(turn, dict))
            history_text = raw_text.lower()

            # --- Destination (known list) ---
            if "destination" not in extracted:
                known_destinations = [
                    "maldives", "bora bora", "tahiti", "fiji", "hawaii", "maui", "honolulu",
                    "bali", "phuket", "thailand", "bangkok", "tokyo", "kyoto", "japan",
                    "sydney", "melbourne", "australia", "new zealand", "queenstown",
                    "paris", "london", "rome", "venice", "florence", "italy", "greece",
                    "santorini", "mykonos", "athens", "croatia", "dubrovnik", "barcelona",
                    "madrid", "spain", "lisbon", "portugal", "amsterdam", "berlin",
                    "budapest", "prague", "vienna", "istanbul", "dubai", "singapore",
                    "new york", "miami", "cancun", "mexico", "caribbean", "bahamas",
                    "alaska", "patagonia", "peru", "machu picchu", "iceland", "norway",
                    "mediterranean", "south africa", "morocco", "egypt", "vietnam",
                    "cambodia", "vancouver", "banff", "los angeles", "san francisco",
                ]
                for dest in known_destinations:
                    if dest in history_text:
                        extracted["destination"] = dest.title()
                        break

            # --- Destination (pattern fallback: "to/visit/in <Place>") ---
            if "destination" not in extracted:
                dest_match = re.search(
                    r"(?:go to|travel to|trip to|fly to|flying to|visit|holiday in|"
                    r"vacation in|honeymoon in|getaway to|head to)\s+"
                    r"([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)",
                    raw_text,
                )
                if dest_match:
                    extracted["destination"] = dest_match.group(1).strip()

            # --- Travel dates / date range ---
            if "date_range" not in extracted:
                months = ("january|february|march|april|may|june|july|august|"
                          "september|october|november|december")
                date_patterns = [
                    rf"(?:late|early|mid|mid-)\s*(?:{months})",
                    rf"(?:{months})\s+\d{{1,2}}\s*(?:-|–|to)\s*\d{{1,2}}",
                    rf"(?:{months})\s+\d{{1,2}}",
                    rf"next\s+(?:{months})",
                    rf"(?:in|during)\s+(?:{months})",
                    rf"\b(?:{months})\b",
                    r"(?:next|this)\s+(?:spring|summer|fall|autumn|winter)",
                    r"\b(?:spring|summer|fall|autumn|winter)\b",
                ]
                for pattern in date_patterns:
                    date_match = re.search(pattern, history_text)
                    if date_match:
                        value = date_match.group().strip().title()
                        extracted["date_range"] = value
                        extracted["travel_dates"] = value
                        break

            # --- Budget (require 3+ digits so day numbers aren't mistaken for budget) ---
            if "budget" not in extracted:
                budget_match = re.search(
                    r"\$\s?[\d,]{3,}|[\d,]{4,}\s*(?:aud|usd|eur|gbp|jpy|"
                    r"us dollars|dollars|euros|pounds|yen)|[\d,]{4,}",
                    history_text,
                )
                if budget_match:
                    extracted["budget"] = budget_match.group().strip()

        return extracted

    def _format_conversation_history(self, context: dict[str, Any]) -> str:
        """Format conversation history for the agent prompt."""
        history = context.get("history", [])
        if not isinstance(history, list) or not history:
            return "(No prior conversation)"
        
        lines = []
        for turn in history[-6:]:  # Last 6 turns for brevity
            if isinstance(turn, dict):
                role = turn.get("role", "unknown").upper()
                text = turn.get("text", "")
                if text:
                    lines.append(f"{role}: {text}")
        
        return "\n".join(lines) if lines else "(No prior conversation)"

    def _format_extracted_fields(self, extracted: dict[str, str]) -> str:
        """Format extracted fields for the agent prompt."""
        if not extracted:
            return "- None yet"
        
        labels = {
            "origin": "Departure city",
            "destination": "Destination",
            "date_range": "Travel dates",
            "travel_dates": "Travel dates",
            "budget": "Budget",
            "region": "Region",
            "interests": "Interests",
            "booking_reference": "Booking reference",
            "support_topic": "Support topic",
        }
        
        lines = []
        for field, value in extracted.items():
            label = labels.get(field, field.replace("_", " ").title())
            lines.append(f"- {label}: {value}")
        
        return "\n".join(lines) if lines else "- None yet"
