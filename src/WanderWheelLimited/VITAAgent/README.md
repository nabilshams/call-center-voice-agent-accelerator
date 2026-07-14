# VITAAgent — Voice-Enabled On-Road Training Assistant

VITA is a conversational training agent for WanderWheels on-road staff. It
delivers hands-free, voice-first training by guiding learners through
role- and country-specific modules, running scripted practice scenarios,
rating performance against a rubric, and adapting the training pace to the
learner's results.

> **Status:** UC-01 scaffold — tools are function stubs. Wire the real
> implementations (Azure AI Search, Cosmos DB, Evaluation agent) before demo.

---

## Architecture

```
Learner (voice) ──► Voice Live / ACS ──► VITAAgent (this agent)
                                              │
                        ┌─────────────────────┼─────────────────────┐
                        ▼                     ▼                     ▼
                  Azure AI Search       Cosmos DB           Evaluation agent
                  (vita-onroad-sop)    (vita-progress)        (UC-06, future)
```

### Tools

| Tool | Description | Status |
|---|---|---|
| `getModule(role, country, section)` | Fetch training section from Azure AI Search | Stub |
| `recordProgress(learnerId, section, status, score)` | Persist completion record to Cosmos DB | Stub |
| `startPractice(scenario)` | Begin a scripted voice practice scenario | Stub |
| `endPractice()` | Close scenario and return transcript for scoring | Stub |
| `requestScore(transcript, rubricId)` | Evaluate responses against rubric (Evaluation agent) | Stub |

### Telemetry

When `APPLICATIONINSIGHTS_CONNECTION_STRING` is set, each tool emits:
- An OpenTelemetry span named `vita.<toolName>` with call latency.
- A structured log entry with `latency_ms` and key parameters.

Wire `requestScore` to also emit a custom App Insights metric
(`vita.learner_score`) once the real evaluation is implemented.

---

## Data

Sample on-road SOP content lives in [`/data/vita/wanderwheels_onroad_sop.md`](../../../data/vita/wanderwheels_onroad_sop.md).
This file is the seed dataset for the `vita-onroad-sop` Azure AI Search index.

Additional sample data:
- [`/data/vita/vita_rubrics.md`](../../../data/vita/vita_rubrics.md) — call-quality rubrics used by `requestScore`.
- [`/data/vita/vita_practice_scenarios.md`](../../../data/vita/vita_practice_scenarios.md) — scripted role-play scenarios used by `startPractice`.

### Loading data into Azure AI Search

1. Create an index named `vita-onroad-sop` with fields: `id`, `role`, `country`,
   `sectionId`, `title`, `content`, `practiceScenarios`, `rubricId`.
2. Import the SOP markdown sections via the Azure portal blob-indexer or the
   Azure AI Search REST API.
3. Set `AZURE_SEARCH_ENDPOINT` in the agent's environment variables.

---

## Running locally

### Prerequisites

- Python 3.12 (local Docker / `azd` runs use `python:3.12-slim`; Foundry deployment targets `python_3_13`)
- An Azure AI Foundry project (`FOUNDRY_PROJECT_ENDPOINT`)
- A `gpt-4o` or `gpt-4o-mini` model deployment (`AZURE_AI_MODEL_DEPLOYMENT_NAME`)

### Option 1: Azure Developer CLI (`azd`)

```bash
# From the repository root
azd ai agent run --service VITAAgent --no-inspector
```

In a separate terminal:

```bash
azd ai agent invoke --local --service VITAAgent "I want to start my training."
```

### Option 2: VS Code (Foundry Toolkit)

1. Open the Command Palette and run **Foundry Toolkit: Create Hosted Agent**.
2. Select this directory as the project root.
3. Press **F5** to start in debug mode.

---

## Deploying to Foundry

```bash
azd deploy VITAAgent --no-prompt
```

---

## Wiring the stubs (next steps)

1. **`getModule`** — query the `vita-onroad-sop` Azure AI Search index.
   Add `AZURE_SEARCH_ENDPOINT` and search client code.

2. **`recordProgress`** — write to Cosmos DB container `vita-progress`.
   Use `DefaultAzureCredential()` with the agent's managed identity.

3. **`startPractice` / `endPractice`** — load scenario scripts from the search
   index and accumulate the practice transcript in Foundry thread state.

4. **`requestScore`** — call the Evaluation agent (UC-06) with the transcript
   and rubric, or use a direct GPT-4o call with the rubric as system context.
   Emit the score as a custom App Insights metric.

5. **Voice interface** — the voice pipeline (ACS + Voice Live) is already
   wired in `server/app/handler/acs_media_handler.py`. Route voice calls to
   VITAAgent by setting `VITA` in the `MAFTravelOrchestrator.ROUTE_TO_AGENT`
   map (already done) and configuring the caller's intent to match `VITA`.

---

## Demo scenario (UC-01)

1. New on-road employee says: **"I want to start my training."**
2. VITA greets them, confirms role and country, and calls `getModule`.
3. Learner asks a question mid-module; VITA answers from retrieved SOP content.
4. VITA calls `startPractice`, runs the scenario, then `endPractice`.
5. VITA calls `requestScore`, reads out the grade and top improvement tip.
6. VITA calls `recordProgress` and advances to the next section.

---

## Related use cases

| UC | Agent | Relationship |
|---|---|---|
| UC-05 | CoachingAgent | Invoked when learner wants to think out loud (future) |
| UC-06 | EvaluationAgent | Scores call quality via `requestScore` (future) |
| UC-07 | MemoryAgent | Per-user memory; VITA reads last position on resume (future) |
