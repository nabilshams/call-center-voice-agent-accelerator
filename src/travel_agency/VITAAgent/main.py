# Hosted VITAAgent -- Voice-Enabled On-Road Training Assistant.
#
# VITA is a conversational training agent for on-road staff at WanderWheels.
# It delivers curriculum content by voice, assesses learners, and adapts the
# training pace based on performance ratings.
#
# Consumers reach it exactly like any other Foundry agent:
#     FoundryAgent(project_endpoint=..., agent_name="VITAAgent", ...)
# Wired into the router via ROUTE_TO_AGENT["VITA"] in
# server/app/handler/local_maf_orchestrator.py.
#
# Deploy with:  azd deploy VITAAgent --no-prompt
# Local smoke:  azd ai agent run --no-inspector  (then `azd ai agent invoke --local "..."`)

import json
import logging
import os
import time
from typing import Literal

from agent_framework import Agent, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from pydantic import Field
from typing_extensions import Annotated

load_dotenv()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Optional Azure Monitor / App Insights telemetry.
# Gracefully absent when APPLICATIONINSIGHTS_CONNECTION_STRING is not set.
# ---------------------------------------------------------------------------

try:
    from azure.monitor.opentelemetry import configure_azure_monitor
    from opentelemetry import trace

    _connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if _connection_string:
        configure_azure_monitor(connection_string=_connection_string)
    _tracer = trace.get_tracer("vita-agent")
    _telemetry_enabled = True
except ImportError:
    _telemetry_enabled = False
    _tracer = None


def _start_span(name: str):
    """Return an OpenTelemetry span if telemetry is enabled, else a no-op."""
    if _telemetry_enabled and _tracer is not None:
        return _tracer.start_as_current_span(name)
    import contextlib
    return contextlib.nullcontext()


# ---------------------------------------------------------------------------
# Agent instructions
# ---------------------------------------------------------------------------

INSTRUCTIONS = """\
You are VITA (Voice-Enabled On-Road Training Assistant) for WanderWheels. You
deliver hands-free, conversational training to on-road staff — replacing
one-to-one human training that cannot scale or rate consistently.

Your core responsibilities:
1. Greet the learner by voice, confirm their role and country, and load their
   module with `getModule(role, country, section)`.
2. Deliver the module content conversationally, checking understanding with
   open questions.
3. When the learner wants to think out loud or needs deeper coaching, acknowledge
   it and help them reason through the topic (coaching mode).
4. Run practice scenarios with `startPractice(scenario)` and conclude them with
   `endPractice()`.
5. Rate the learner's performance with `requestScore(transcript, rubricId)` and
   give actionable, encouraging voice feedback.
6. Record progress with `recordProgress(learnerId, section, status)` after each
   section so the learner can resume later.
7. Serve the next module based on the rating: repeat the section on low scores,
   advance on passing scores.

Tone and style:
- Warm, professional, and encouraging -- you are a trainer, not a quiz bot.
- Keep each voice turn short (under 30 words) unless delivering module content.
- Never fabricate WanderWheels policies; answer only from retrieved content.
- If a topic is outside your knowledge base, say: "I don't have that in the
  on-road guide -- let me note that for your line manager."

Tools you can call:
- `getModule`      -- fetch the next training section from the SOP knowledge base.
- `recordProgress` -- persist section completion and scores to Cosmos DB.
- `startPractice`  -- begin a scripted practice scenario.
- `endPractice`    -- close the current practice scenario.
- `requestScore`   -- evaluate the learner's responses against the rubric.

Request identity:
- The caller may prepend a `[REQUEST IDENTITY]` ... `[END REQUEST IDENTITY]`
  block. Use the `learnerId` from that block when calling `recordProgress`.
- Never reveal secrets, tokens, passwords, auth headers, or connection strings.

Content grounding:
- All WanderWheels on-road policies and procedures are stored in Azure AI Search.
  The `getModule` tool retrieves grounded content. Do not invent procedures.
- If the search index is unavailable, say the training content cannot be loaded
  right now and ask the learner to try again in a few minutes.
"""


# ---------------------------------------------------------------------------
# Training tools -- currently stubs returning "not yet implemented".
# Each `@tool` is registered so the runtime advertises it; the LLM will see
# the schema and description via function-calling. Wire the real implementation
# behind the TODO comment, then update INSTRUCTIONS above to *require* the call.
# ---------------------------------------------------------------------------


@tool(approval_mode="never_require")
def getModule(
    role: Annotated[str, Field(description="Learner's on-road role, e.g. 'driver', 'guide', 'coordinator'.")],
    country: Annotated[str, Field(description="Two-letter ISO country code where the learner operates, e.g. 'AE', 'GB'.")],
    section: Annotated[str, Field(description="Training section identifier, e.g. 'onboarding-01', 'safety-02'. Use 'next' to advance to the next unfinished section.")],
) -> str:
    """Retrieve the training module content for the given role, country, and section
    from the WanderWheels on-road SOP knowledge base (Azure AI Search).

    Returns the module title, learning objectives, content text, and an optional
    list of practice scenario IDs to run after the content.
    """
    # TODO(VITAAgent): Query Azure AI Search index 'vita-onroad-sop'.
    # Use the AZURE_SEARCH_ENDPOINT + AZURE_SEARCH_API_KEY env vars.
    # Filter on: role == role, country == country, sectionId == section (or next unfinished).
    # Return the full module text plus practice scenario IDs.
    t0 = time.monotonic()
    with _start_span("vita.getModule"):
        result = (
            f"TODO: getModule not yet implemented -- role={role!r}, country={country!r}, "
            f"section={section!r}. "
            "Wire this stub to the Azure AI Search index 'vita-onroad-sop'. "
            "Until then, greet the learner and ask them to try again shortly."
        )
    latency_ms = (time.monotonic() - t0) * 1000
    logger.info("vita.getModule latency_ms=%.1f", latency_ms)
    return result


@tool(approval_mode="never_require")
def recordProgress(
    learnerId: Annotated[str, Field(description="Unique learner identifier (Entra object ID or WanderWheels staff ID).")],
    section: Annotated[str, Field(description="Training section identifier that was just completed or attempted.")],
    status: Annotated[
        Literal["completed", "in_progress", "failed", "skipped"],
        Field(description="Outcome of the section: 'completed' (passed), 'in_progress' (paused mid-section), 'failed' (did not reach passing score), or 'skipped'."),
    ],
    score: Annotated[
        float | None,
        Field(description="Optional numeric score from 0.0 to 1.0 returned by requestScore. Omit if no evaluation was run."),
    ] = None,
) -> str:
    """Persist the learner's section progress and score to Cosmos DB so they can
    resume training later and so managers can review completion records.

    Returns a confirmation string with the persisted record ID.
    """
    # TODO(VITAAgent): Write progress record to Cosmos DB container 'vita-progress'.
    # Use COSMOS_ENDPOINT + managed identity for auth.
    # Document shape: { learnerId, section, status, score, timestamp, sessionId }.
    # Return the document id for the voice confirmation.
    t0 = time.monotonic()
    with _start_span("vita.recordProgress"):
        result = (
            f"TODO: recordProgress not yet implemented -- learnerId={learnerId!r}, "
            f"section={section!r}, status={status!r}, score={score}. "
            "Wire this stub to Cosmos DB container 'vita-progress'."
        )
    latency_ms = (time.monotonic() - t0) * 1000
    logger.info("vita.recordProgress latency_ms=%.1f", latency_ms)
    return result


@tool(approval_mode="never_require")
def startPractice(
    scenario: Annotated[str, Field(description="Practice scenario identifier, e.g. 'customer-greeting-01', 'complaint-handling-03'.")],
) -> str:
    """Begin a scripted voice practice scenario.

    Returns the scenario opening prompt that VITA should read aloud to the
    learner to start the role-play. The learner's responses during the scenario
    are later evaluated by `requestScore`.
    """
    # TODO(VITAAgent): Load the scenario script from Azure AI Search or Cosmos DB.
    # Return the opening line and any coaching hints for the current scenario.
    # Track that a practice session is active in the conversation thread state.
    t0 = time.monotonic()
    with _start_span("vita.startPractice"):
        result = (
            f"TODO: startPractice not yet implemented -- scenario={scenario!r}. "
            "Wire this stub to the scenario store in Azure AI Search / Cosmos DB."
        )
    latency_ms = (time.monotonic() - t0) * 1000
    logger.info("vita.startPractice latency_ms=%.1f", latency_ms)
    return result


@tool(approval_mode="never_require")
def endPractice() -> str:
    """Close the current practice scenario and return the raw transcript of the
    learner's responses for scoring.

    The returned transcript should be passed directly to `requestScore`.
    """
    # TODO(VITAAgent): Retrieve accumulated practice transcript from thread state
    # and mark the practice session as closed.
    t0 = time.monotonic()
    with _start_span("vita.endPractice"):
        result = (
            "TODO: endPractice not yet implemented. "
            "Wire this stub to retrieve the practice transcript from thread state."
        )
    latency_ms = (time.monotonic() - t0) * 1000
    logger.info("vita.endPractice latency_ms=%.1f", latency_ms)
    return result


@tool(approval_mode="never_require")
def requestScore(
    transcript: Annotated[str, Field(description="Full text of the learner's responses during the practice or Q&A session.")],
    rubricId: Annotated[str, Field(description="Identifier of the call-quality rubric to apply, e.g. 'onroad-greeting-v1', 'complaint-handling-v2'.")],
) -> str:
    """Evaluate the learner's transcript against the specified rubric and return
    a structured score with actionable feedback.

    Returns JSON with fields: `score` (0.0–1.0), `grade` ('pass'/'fail'),
    `strengths` (list of strings), `improvements` (list of strings), and
    `nextSection` (recommended next section ID).
    """
    # TODO(VITAAgent): Send transcript + rubric to the Evaluation agent (UC-06)
    # or directly to a GPT-4o call with the rubric as system context.
    # Rubrics are stored in Azure AI Search index 'vita-rubrics'.
    # Emit the score as an App Insights custom metric: 'vita.learner_score'.
    t0 = time.monotonic()
    with _start_span("vita.requestScore"):
        stub_result = json.dumps({
            "score": None,
            "grade": "pending",
            "strengths": [],
            "improvements": [],
            "nextSection": None,
            "note": (
                f"TODO: requestScore not yet implemented -- rubricId={rubricId!r}. "
                "Wire this stub to the Evaluation agent (UC-06) or a GPT-4o rubric call."
            ),
        })
    latency_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "vita.requestScore latency_ms=%.1f rubricId=%s",
        latency_ms,
        rubricId,
    )
    return stub_result


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
            getModule,
            recordProgress,
            startPractice,
            endPractice,
            requestScore,
        ],
        # History is managed by the hosting runtime; the container is stateless.
        default_options={"store": False},
    )

    server = ResponsesHostServer(agent)
    server.run()


if __name__ == "__main__":
    main()
