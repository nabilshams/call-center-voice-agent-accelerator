# Travel Agency App — Context Reference

> **Purpose:** Single source of truth for the travel-agency slice of this repo.
> Any coding session should be able to read this doc and understand what
> exists, where things live, and how the pieces fit together — without
> spelunking the codebase.
>
> **Update convention:** When we make a non-trivial change (new agent, new
> handler, new env var, layout shift, gotcha discovered), add a bullet to
> the **Change log** at the bottom and update the relevant section above.
> Keep it factual and terse. If a section grows past ~two screens, split it.

**Last verified against code:** 2026-07-06.

---

## Table of contents

1. [What this app is](#what-this-app-is)
2. [Runtime architecture](#runtime-architecture)
3. [Directory map](#directory-map)
4. [Foundry project & agent inventory](#foundry-project--agent-inventory)
5. [Prompt-agent lifecycle (source-of-truth on disk)](#prompt-agent-lifecycle-source-of-truth-on-disk)
6. [Hosted agent (TripPlannerAgent)](#hosted-agent-tripplanneragent)
7. [Evaluation scaffolding](#evaluation-scaffolding)
8. [Configuration & environment variables](#configuration--environment-variables)
9. [Local dev cheat sheet](#local-dev-cheat-sheet)
10. [Known constraints & gotchas](#known-constraints--gotchas)
11. [Change log](#change-log)

---

## What this app is

The travel-agency app is one of several personas the Call Center Voice Agent Accelerator can inhabit. It simulates a **House of Travel** contact-centre experience where a customer can either:

- **Call in via ACS** (Azure Communication Services) — a phone-style voice conversation that streams through the Voice Live real-time model, or
- **Use the web** — either a browser voice UI ([server/static/travel-support.html](server/static/travel-support.html)) or a text chat UI ([server/static/travel-chat.html](server/static/travel-chat.html)).

Both channels are backed by the **same orchestrator** ([server/app/handler/local_maf_orchestrator.py](server/app/handler/local_maf_orchestrator.py)), which routes user intent to specialist Foundry agents (flights, holiday packages, cruises, tours, consultants, deals, etc.). The orchestrator delegates all product knowledge, tone, and slot-filling to the agents in Foundry — the code is a thin routing + fan-out layer.

There are **two other personas** in the same repo (MMH / medical-mutual-help, cybersecurity, clinician-notes) but this doc covers **only the travel-agency lane**.

---

## Runtime architecture

### Voice channel (ACS phone + browser voice)

```
Caller / browser mic
        │  (audio frames over WebSocket)
        ▼
ACSMediaHandler ([server/app/handler/acs_media_handler.py](server/app/handler/acs_media_handler.py))
        │  ├─ streams audio to Voice Live model (Azure OpenAI real-time)
        │  ├─ receives model events (transcripts, tool calls, audio deltas)
        │  └─ persona injection via personas.py (persona_context = "travel")
        │
        │  Model calls consult_travel_specialists tool
        ▼
LocalMAFTravelOrchestrator ([server/app/handler/local_maf_orchestrator.py](server/app/handler/local_maf_orchestrator.py))
        │  ├─ orchestrate_multi(context) → routes via OrchestratorAgent / Multi-IntentOrchestrator (Foundry prompt agents)
        │  ├─ fans out to specialists in parallel (FlightBooking, HolidayPackage, ...)
        │  └─ returns spoken_reply + selected_agents + workflow_trace
        │
        ▼
Voice Live model speaks the aggregated reply back to caller.
Routing log rendered live in browser via WS message
{"Kind":"OrchestratorResponse", ...} or {"Kind":"DirectResponse", ...}
```

Key rules (see repo memory notes #21, #23 for history):
- **Direct answers** (greetings, small talk, acknowledgments): model handles inline. It emits `{"Kind":"DirectResponse"}` for the routing log so the UI shows "Answered directly — no specialist consulted".
- **Any travel product query** (availability, pricing, options, recommendations, itineraries, bookings): model MUST call `consult_travel_specialists`; specialists own all slot-filling and follow-up questions.
- **No shadow orchestrator calls.** UI (travel-support.html) no longer calls `/travel/orchestrate` on every UserTranscription — the routing log is driven exclusively by the real tool call. This eliminates the double-consult bug from the earlier version.

### Text chat channel

```
Browser (travel-chat.html)
        │  POST /travel/orchestrate  {message, history[:-1][-8:], channel:"chat-web"}
        ▼
LocalMAFTravelOrchestrator.orchestrate_multi
        │  (same code path as voice, but channel="chat-web")
        ▼
JSON response: {spoken_reply, selected_agents, workflow_trace, ...}
        │
        ▼
UI renders Markdown reply + routing log
```

The specialist prompt is **channel-aware**: voice channel gets a short TTS-safe envelope; text channel with history gets a wrapping "Conversation history … Current user request …" prompt; text channel first-turn sends the message verbatim (matches Foundry playground output).

### Orchestrator modes

`TRAVEL_ORCHESTRATOR_MODE` env var — only **two** valid values (consolidated in prior work, note #26):
- `maf` (default): in-process `LocalMAFTravelOrchestrator` calling Foundry agents via `agent_framework.foundry.FoundryAgent`.
- `foundry`: remote Foundry workflow via `FoundryWorkflowClient` (used with a Foundry-side workflow).

Both fail loudly on error (no silent fallback). Unknown mode → 400.

---

## Directory map

```
call-center-voice-agent-accelerator/
├── docs/
│   ├── recap-features.md
│   └── travel-agency.md              ← this file
├── infra/                            Bicep / azd deployment
├── azure.yaml                        Registers every agent as a `host: azure.ai.agent` service
├── src/
│   └── travel_agency/                All Foundry agents for the TravelAgency project
│       ├── _shared/
│       │   └── evaluators/
│       │       └── behavioral_adherence.yaml   Shared custom evaluator
│       ├── TripPlannerAgent/         Hosted agent (azd-deployed container)
│       │   ├── agent.yaml
│       │   ├── main.py
│       │   ├── requirements.txt
│       │   ├── Dockerfile
│       │   └── README.md
│       ├── ConsultantMatchAgent/     agent.yaml + eval.yaml + dataset.jsonl
│       ├── CruiseDiscoveryAgent/     agent.yaml + eval.yaml + dataset.jsonl
│       ├── DealAlertAgent/           agent.yaml + eval.yaml + dataset.jsonl
│       ├── FlightBookingAgent/       agent.yaml + eval.yaml + dataset.jsonl (tool-using)
│       ├── GeneralFAQAgent/          agent.yaml + eval.yaml + dataset.jsonl
│       ├── HolidayPackageAgent/      agent.yaml + eval.yaml + dataset.jsonl
│       ├── MultiIntentOrchestrator/  agent.yaml
│       ├── OrchestratorAgent/        agent.yaml
│       ├── PostBookingCocierge/      agent.yaml + eval.yaml + dataset.jsonl (spelling matches Foundry — note #24)
│       ├── TourMatchingAgent/        agent.yaml + eval.yaml + dataset.jsonl
│       └── TravelInspirationAgent/   agent.yaml + eval.yaml + dataset.jsonl
├── server/
│   ├── server.py                     Quart entrypoint; wires routes + handlers
│   ├── Dockerfile
│   ├── pyproject.toml                uv-managed deps
│   ├── scripts/
│   │   ├── apply_prompt_agents.py    Local → Foundry (skips unchanged specs)
│   │   ├── dump_prompt_agents.py     Foundry → local
│   │   ├── poll_eval_run.py          Kick + wait for a batch eval run
│   │   ├── download_eval_results.py  Pull run rows locally
│   │   └── analyze_eval_results.py   Summarize scores + failing rows
│   ├── app/
│   │   ├── api/
│   │   │   ├── travel_agency/        Domain code (data, personas, specs)
│   │   │   └── foundry_admin/        Admin blueprints (agents + prompt-agents CRUD)
│   │   ├── foundry/                  Reusable Foundry-agent library
│   │   │   ├── agent_manager.py      Classic (Assistants) + prompt CRUD
│   │   │   ├── definition.py         Load/apply prompt-agent YAML with drift detect
│   │   │   ├── exceptions.py
│   │   │   └── models.py             AgentSpec / PromptAgentSpec / *Info dataclasses
│   │   └── handler/
│   │       ├── acs_media_handler.py         Voice Live orchestration + tool dispatch
│   │       ├── acs_event_handler.py         ACS incoming-call webhooks
│   │       ├── local_maf_orchestrator.py    Travel orchestrator (this app's brain)
│   │       ├── foundry_workflow_client.py   Remote workflow client (foundry mode)
│   │       ├── personas.py                  MMH vs travel persona injection
│   │       ├── voice_live_transport.py      Voice Live WS transport
│   │       ├── webrtc_transport.py
│   │       ├── websocket_transport.py
│   │       ├── ambient_mixer.py             Background call-centre ambience
│   │       ├── recap_inference.py           Post-call recap generation
│   │       └── speech_transcription_handler.py
│   ├── scripts/
│   │   ├── apply_prompt_agents.py    Write on-disk agent.yaml → Foundry (drift-aware)
│   │   └── dump_prompt_agents.py     Foundry → on-disk agent.yaml (round-trip clean)
│   ├── static/                       Web UIs (voice, chat, transcript viewers, recap)
│   ├── tests/                        pytest tests
│   └── transcriptions/               Fixture transcripts per persona
├── azure.yaml                        azd service map (TripPlannerAgent + app)
├── ai-services.json
├── role_assignments.json
└── README.md                         Top-level deployment README
```

---

## Foundry project & agent inventory

- **Project:** `TravelAgency` in the AI Foundry account for this deployment.
- **Endpoint:** `https://domain-ccvaa2-imlai.services.ai.azure.com/api/projects/TravelAgency` (custom subdomain — NOT the account-name subdomain).
- **Judge/inference model:** `gpt-4o-mini` (env var `MAF_MODEL`, referenced as `${MAF_MODEL}` in all YAML).

### Agent inventory (11 prompt agents + 1 hosted agent)

All agents live under [src/travel_agency/](src/travel_agency/) — folder name matches the Foundry agent name.

| # | Foundry name | Folder | Role | Kind |
|---|---|---|---|---|
| 1 | `OrchestratorAgent` | `OrchestratorAgent/` | Single-intent router → emits `ROUTE_TO_AGENT` token | prompt |
| 2 | `Multi-IntentOrchestrator` | `MultiIntentOrchestrator/` | Multi-intent router → emits comma-separated tokens | prompt |
| 3 | `FlightBookingAgent` | `FlightBookingAgent/` | Flights (search + slot-fill + confirm; hands off booking) — has MCP tool | prompt |
| 4 | `HolidayPackageAgent` | `HolidayPackageAgent/` | Package holidays discovery (one-question-at-a-time; max 3 options; max 1 upsell) | prompt |
| 5 | `CruiseDiscoveryAgent` | `CruiseDiscoveryAgent/` | Cruise line matching (Princess / Carnival / AmaWaterways / etc.) | prompt |
| 6 | `TourMatchingAgent` | `TourMatchingAgent/` | Escorted tour operator matching (Contiki / Globus / Intrepid / …) | prompt |
| 7 | `TravelInspirationAgent` | `TravelInspirationAgent/` | Undecided-customer inspiration | prompt |
| 8 | `Post-BookingCocierge` | `PostBookingCocierge/` | Post-booking concierge (real misspelling — note #24) | prompt |
| 9 | `ConsultantMatchAgent` | `ConsultantMatchAgent/` | Human consultant matching / escalation | prompt |
| 10 | `DealAlertAgent` | `DealAlertAgent/` | Current deals & promotions | prompt |
| 11 | `GeneralFAQAgent` | `GeneralFAQAgent/` | Payments, gift cards, insurance, app help, policies, advisories | prompt |
| 12 | `TripPlannerAgent` | `TripPlannerAgent/` | Multi-day itinerary planning (hosted) | hosted |

`ROUTE_TO_AGENT` mapping in [local_maf_orchestrator.py](server/app/handler/local_maf_orchestrator.py) must stay in sync with the Foundry side — agent names are keys.

**Router-side gotcha:** For any new specialist route (e.g., `TRIP_PLANNER`, `GENERAL_FAQ`) to actually be selected, both `OrchestratorAgent` and `Multi-IntentOrchestrator` prompts in Foundry must be updated to emit that token. Adding a code-side mapping alone is not enough.

---

## Prompt-agent lifecycle (source-of-truth on disk)

Prompt agents live under [src/travel_agency/](src/travel_agency/) as `<AgentName>/agent.yaml` alongside the hosted [src/travel_agency/TripPlannerAgent/](src/travel_agency/TripPlannerAgent/). The `src/travel_agency/` folder groups every agent that belongs to the TravelAgency Foundry project. Two scripts manage round-trip with Foundry:

### Push local → Foundry

```powershell
python server/scripts/apply_prompt_agents.py src/travel_agency/                              # all agents, dry-run OFF
python server/scripts/apply_prompt_agents.py src/travel_agency/ --dry-run                    # preview
python server/scripts/apply_prompt_agents.py src/travel_agency/FlightBookingAgent/           # scoped
```

Behavior:
- Loads every `agent.yaml` matching `kind: prompt`.
- For each: fetches the latest live version, compares via `spec_matches_existing` (portal metadata stripped, per-line whitespace normalized). Skips create if identical → **no spurious new versions**.
- Otherwise calls `create_version(name, body)` → new immutable version.

### Pull Foundry → local

```powershell
python server/scripts/dump_prompt_agents.py                                                  # all agents to <repo>/src/travel_agency/
python server/scripts/dump_prompt_agents.py --agent FlightBookingAgent --force
python server/scripts/dump_prompt_agents.py --dry-run
```

Behavior:
- Lists all prompt agents on the project, writes one per PascalCase folder under `src/travel_agency/`.
- Uses `${MAF_MODEL}` placeholder when live model matches env; forces `|` block-scalar for instructions (per-line rstrip); strips portal-injected metadata (`logo`, `modified_at`, `microsoft.*`, empty `description`).
- Refuses to overwrite existing files unless `--force`.

### Loader ([server/app/foundry/definition.py](server/app/foundry/definition.py))

- `load_definition(path)` → `LoadedDefinition` with a validated `PromptAgentSpec` (or hosted variant).
- `discover_definitions(paths, kinds=...)` — directory scan; skips files without an explicit `kind:` field (this is what prevents co-located `eval.yaml` from breaking `apply`).
- `spec_matches_existing(spec, live_agent)` — the drift check; strips portal metadata before comparing.
- `_normalize_text` rstrips **per line** so trailing whitespace from portal-authored prompts doesn't diff spuriously.

### Admin HTTP surface

Two Quart blueprints under [server/app/api/foundry_admin/](server/app/api/foundry_admin/) provide `X-Admin-Key`-gated CRUD:
- `/api/foundry/agents` — classic (Assistants) agents.
- `/api/foundry/prompt-agents` — prompt agents (POST creates a new version if name exists).

Both require env vars `FOUNDRY_ADMIN_ENABLED=true`, non-empty `ADMIN_API_KEY`, and `MAF_PROJECT_ENDPOINT` — else the routes are not registered.

---

## Hosted agent (TripPlannerAgent)

Different lifecycle from the prompt agents:
- Lives at [src/travel_agency/TripPlannerAgent/](src/travel_agency/TripPlannerAgent/) with its own `agent.yaml`, `main.py`, `requirements.txt`, `Dockerfile`, `.agentignore`.
- Registered as a service in [azure.yaml](azure.yaml) under `services.TripPlannerAgent` (`host: azure.ai.agent`).
- Deploys with `azd deploy TripPlannerAgent`.
- Has 4 tool stubs (Places / Bookings / Weather / CustomerStore) — env vars are commented out in `agent.yaml`, tools not yet integrated.
- Router mapping exists (`ROUTE_TO_AGENT["TRIP_PLANNER"] = "TripPlannerAgent"`) but until `OrchestratorAgent` / `Multi-IntentOrchestrator` emit the `TRIP_PLANNER` token, it won't be selected in production traffic.

---

## Evaluation scaffolding

**Layout:** every Foundry agent (hosted **and** prompt) lives under `src/travel_agency/<AgentName>/` — the `travel_agency` folder groups everything that belongs to the TravelAgency Foundry project. Each agent folder owns its own eval assets alongside `agent.yaml`. All entries are registered as services in [azure.yaml](azure.yaml) with `host: azure.ai.agent` so `azd ai agent eval run` can scope by service name.

```
src/travel_agency/
├── _shared/evaluators/
│   └── behavioral_adherence.yaml   Shared custom evaluator (1–5 rubric)
├── TripPlannerAgent/                 Hosted (container-deployed)
├── FlightBookingAgent/               Prompt agent + eval scaffold
│   ├── agent.yaml
│   ├── eval.yaml                     Suite intent (built-in evaluators + custom + tool-call-accuracy)
│   └── dataset.jsonl                 10 hand-crafted rows
├── HolidayPackageAgent/              (eval scaffold, 8 rows)
├── CruiseDiscoveryAgent/             (eval scaffold, 8 rows)
├── TourMatchingAgent/                (eval scaffold, 8 rows)
├── ConsultantMatchAgent/             (eval scaffold, 8 rows)
├── DealAlertAgent/                   (eval scaffold, 8 rows)
├── GeneralFAQAgent/                  (eval scaffold, 8 rows)
├── PostBookingCocierge/              (eval scaffold, 8 rows)
├── TravelInspirationAgent/           (eval scaffold, 8 rows)
└── … (router agents: OrchestratorAgent, MultiIntentOrchestrator — no
  answer-quality eval scaffold yet; these need route-label accuracy suites)
```

**eval.yaml shape (prompt-agent flavor):**

```yaml
name: <suite-name>
agent:
  name: <FoundryAgentName>
  kind: prompt              # adapted from hosted-focused skill schema
dataset:
  local_uri: dataset.jsonl
evaluators:
  - relevance               # built-in fallback baseline
  - task_adherence
  - intent_resolution
  - indirect_attack
  - builtin.tool_call_accuracy   # only for tool-using agents (FlightBooking)
  - name: behavioral_adherence
    local_uri: ../_shared/evaluators/behavioral_adherence.yaml
options:
  eval_model: ${MAF_MODEL}
```

**dataset.jsonl row shape:**

```json
{"query": "...", "expected_behavior": "..."}
```

Optional `context` / `ground_truth` fields per built-in evaluator needs.

**Custom evaluator output contract:** the runtime enforces `{"result", "reason"}`. Do NOT add any `Return JSON` block to `behavioral_adherence.yaml` — it will collide and reject the run.

**Runtime cache:** `.foundry/` under each agent root is gitignored (populated by the Foundry observe skill / `azd ai agent eval` — regenerable from source-controlled files).

### Running an eval

Three interchangeable paths — pick the one that matches your context.

#### 1. `azd` CLI (interactive dev loop — preferred locally)

```powershell
cd src/travel_agency/FlightBookingAgent
azd ai agent eval run --config eval.yaml --name flight-booking-smoke-v13-baseline
```

- Path resolution: `--config` is relative to the azd service's `project:` in [azure.yaml](azure.yaml). Because every agent is registered as a `host: azure.ai.agent` service under `src/travel_agency/<AgentName>`, running from inside the agent folder just works.
- With no `--service` flag, `--no-prompt` picks the **first service alphabetically** (`ConsultantMatchAgent`). Omit `--no-prompt` and the picker will list all 12 — select the target explicitly.
- Other useful subcommands: `azd ai agent eval list`, `azd ai agent eval show <run-name>`, `azd ai agent eval generate`, `azd ai agent eval update`.

#### 2. Foundry MCP tools (from a chat agent)

Best when composing multi-step workflows without leaving VS Code.

```
mcp_microsoft_mac_evaluation_agent_batch_eval_create
  agentName: FlightBookingAgent
  evalConfigPath: src/travel_agency/FlightBookingAgent/eval.yaml
```

- Targets the **existing** agent by name — never creates/mutates the agent itself.
- If `behavioral_adherence` isn't yet registered on the project, run `mcp_microsoft_mac_evaluator_catalog_create` first (only needs to happen once per project).

#### 3. Python script chain (CI-friendly, non-interactive)

Wraps the MCP calls in scripts so a run can be launched, polled, downloaded, and analyzed without a chat session:

```powershell
# Kick off + wait
python server/scripts/poll_eval_run.py `
  --agent FlightBookingAgent `
  --eval-config src/travel_agency/FlightBookingAgent/eval.yaml `
  --name flight-booking-smoke-v13-baseline

# Pull results locally
python server/scripts/download_eval_results.py --name flight-booking-smoke-v13-baseline

# Summarize scores + failing rows
python server/scripts/analyze_eval_results.py --name flight-booking-smoke-v13-baseline
```

Use this path in any environment where the CLI picker is unusable (headless CI, no `azd` login, batched regressions).

### Publishing prompt-agent changes (round-trip)

Eval only measures what's live in Foundry. To publish a local `agent.yaml` change before re-running eval:

```powershell
python server/scripts/apply_prompt_agents.py src/travel_agency/FlightBookingAgent/ --dry-run
python server/scripts/apply_prompt_agents.py src/travel_agency/FlightBookingAgent/
```

- Skips unchanged specs (portal metadata stripped, per-line whitespace normalized) — no spurious new versions.
- Do **not** use `azd deploy <PromptAgent>` for prompt agents — they have no Dockerfile and the deploy will fail. `azd deploy TripPlannerAgent` is the correct command for the hosted agent only.

---

## Configuration & environment variables

Populated from [server/.env-sample.txt](server/.env-sample.txt). Key travel-lane vars:

| Var | Purpose | Notes |
|---|---|---|
| `TRAVEL_ORCHESTRATOR_MODE` | `maf` (default) or `foundry` | Fails loudly if unknown |
| `MAF_PROJECT_ENDPOINT` | Foundry project endpoint URL | Must be the custom subdomain (`domain-ccvaa2-imlai...`), NOT the account-name subdomain |
| `MAF_MODEL` | Judge / inference model | `gpt-4o-mini` |
| `AZURE_CLIENT_ID` | Managed identity client ID | Used by `DefaultAzureCredential(managed_identity_client_id=...)` |
| `AZURE_VOICE_LIVE_ENDPOINT` | Voice Live real-time endpoint | Set in Container App env |
| `ADMIN_API_KEY` + `FOUNDRY_ADMIN_ENABLED` | Guards `/api/foundry/*` admin routes | Both required or admin routes not registered |

Removed / never revive: `MAF_WORKFLOW_*` (deleted when consolidating orchestrator modes, note #26).

---

## Local dev cheat sheet

Assumes venv at `.venv/` at repo root (`c:\workspace\csa-accelerators\call-center-voice-agent-accelerator\.venv`).

```powershell
# Activate
& .\.venv\Scripts\Activate.ps1

# Install server deps
Set-Location server
uv sync                                  # or: pip install -e .

# Also install SDKs needed by scripts/tests (NOT in pyproject — ship transitively via Dockerfile pin, note #38)
pip install "azure-ai-agents" "azure-ai-projects>=2.2.0" pytest pytest-asyncio
pip install --pre "agent-framework-foundry==1.0.0rc6"

# Run server locally
uv run server.py                         # open http://127.0.0.1:8000

# Prompt-agent round-trip
python scripts/apply_prompt_agents.py ../src/travel_agency/ --dry-run
python scripts/dump_prompt_agents.py --dry-run

# Tests
python -m pytest tests/test_foundry_definition.py -q
python -m pytest tests/ -q               # full server test suite
```

### Deploy

```powershell
azd provision                            # infra (Bicep)
azd deploy                               # both TripPlannerAgent + app service
azd deploy app                           # just the container app
azd deploy TripPlannerAgent              # just the hosted agent
```

---

## Known constraints & gotchas

- **DNS / endpoint:** Always use the **custom subdomain** for `MAF_PROJECT_ENDPOINT` (e.g., `https://domain-ccvaa2-imlai.services.ai.azure.com/api/projects/TravelAgency`), NOT the account-name subdomain. Wrong endpoint → 404 or missing agents. (Note #14.)
- **agent-framework version skew** is the #1 native-SDK failure mode. Pin `--pre agent-framework==1.0.0rc6` + `agent-framework-foundry==1.0.0rc6` (co-versioned). The "stable" `agent-framework` on PyPI is a DIFFERENT package. Dockerfile is already correctly pinned. (Notes #12, #15.)
- **Prompt agents ≠ classic (Assistants) agents.** `AgentsClient.list_agents()` will NOT return prompt agents — use `AIProjectClient.agents` (`AgentsOperations`) with `AgentKind.PROMPT`. (Note #14.)
- **`Post-BookingCocierge` misspelling is real** — that's the actual agent name in Foundry. Don't "correct" it. (Note #24.)
- **Portal metadata drift:** every UI edit in the Foundry portal injects `logo`, `modified_at`, `microsoft.*` keys. Our loader + dump script strip these — do NOT commit them or add them to the on-disk YAML. (Note #37.)
- **Router-token bootstrapping:** adding a new specialist to `ROUTE_TO_AGENT` in code is a no-op until `OrchestratorAgent` / `Multi-IntentOrchestrator` prompts in Foundry are updated to emit the corresponding token. (Notes #30, #35.)
- **Chat UI history duplication:** `travel-chat.html` must slice `conversationTurns.slice(0, -1).slice(-8)` when building the `history` payload — else the current message ends up in history AND as the current request, and specialists give weird clarifying questions. (Note #18.)
- **Voice pipeline is unified.** Routing log is driven by the real tool call, NOT a shadow `/travel/orchestrate` call. Do not re-add UserTranscription → orchestrator calls in `travel-support.html`. (Note #21.)
- **Fail-loud is the design.** `foundry` mode returns 503 if unconfigured and 502 on `FoundryWorkflowError`; `maf` mode raises `FoundryAgentError` if the router / specialist runs fail. No silent fallback. (Notes #13, #26.)
- **Local SDK gap:** `server/pyproject.toml` does not list `azure-ai-agents` / `azure-ai-projects` / `agent-framework-foundry`. These ship transitively via the Dockerfile pin in prod, but locally you must `pip install` them into `.venv` to run the scripts or tests. (Note #38.)
- **Loader kind-filter:** `discover_definitions` silently skips YAML files with no `kind:` field (e.g., co-located `eval.yaml`). If you author a new agent YAML and forget `kind: prompt`, it will vanish from `apply` output — not error. Explicit `load_definition(path)` still errors. (Note #41.)

---

## Change log

Add newest entries at the top. Include date and short bullet. Reference the section(s) updated.

- **2026-07-06** — Expanded specialist eval scaffolding to five more prompt agents: ConsultantMatchAgent, DealAlertAgent, GeneralFAQAgent, PostBookingCocierge, and TravelInspirationAgent. Each now has `eval.yaml` plus 8 hand-authored `dataset.jsonl` rows using the shared `behavioral_adherence` rubric and built-in baseline evaluators. Remaining unscaffolded prompt agents are the routers (OrchestratorAgent, MultiIntentOrchestrator), which need route-label accuracy suites rather than answer-quality scoring. See [Evaluation scaffolding](#evaluation-scaffolding).
- **2026-07-03** — **Grouped all TravelAgency agents under `src/travel_agency/`.** Moved 12 agent folders + `_shared/` from `src/<AgentName>/` → `src/travel_agency/<AgentName>/`. Rationale: `src/` will hold multiple Foundry projects long-term; naming the container after the Foundry project (`TravelAgency`) keeps them cleanly separated and makes the intent explicit. Updated all `project:` paths in [azure.yaml](azure.yaml), script docstrings + defaults ([apply_prompt_agents.py](server/scripts/apply_prompt_agents.py), [dump_prompt_agents.py](server/scripts/dump_prompt_agents.py) `--target` now `<repo>/src/travel_agency/`), [local_maf_orchestrator.py](server/app/handler/local_maf_orchestrator.py) comment. Eval-yaml `../_shared/evaluators/...` relative refs stay valid (sibling relationship preserved).
- **2026-07-03** — **Consolidated all Foundry agents under `src/`.** Moved 11 prompt-agent folders + `_shared/` from `server/agents/` → `src/<AgentName>/` (PascalCase to match Foundry agent names). Registered every agent in [azure.yaml](azure.yaml) as `host: azure.ai.agent` services so `azd ai agent eval run --config eval.yaml` resolves per-agent inside each folder. Publishing prompt-agent versions still goes through `scripts/apply_prompt_agents.py` — do not run `azd deploy <PromptAgent>` for those entries.
- **2026-07-03** — Applied targeted prompt fixes based on eval findings. Added prominent "CRITICAL RULES" blocks near the top of 3 specialist prompts: **FlightBookingAgent v12→v13** (no stacked questions + explicit frustration-triggers → consultant handoff), **HolidayPackageAgent v2→v3** (inclusions+exclusions always paired, hard max 3 packages, hard max 1 upsell), **CruiseDiscoveryAgent v2→v3** (never accept payment, always end with consultant handoff, first-timer education first). Rules already existed in each prompt but were buried in bullet lists — hoisting to a top-of-prompt CRITICAL block with BAD/GOOD examples gives the LLM highest attention weight for the behaviors that mattered most in the baseline eval. Applied via `python server/scripts/apply_prompt_agents.py src/` (declarative flow).
- **2026-07-03** — Baseline eval run landed for all 4 specialists against live TravelAgency project. `behavioral_adherence` custom evaluator registered in Foundry catalog as v1 (reusable across future runs). Added `server/scripts/{poll_eval_run.py, download_eval_results.py, analyze_eval_results.py}` — per-agent artifacts under `src/<AgentName>/.foundry/results/travel-agency/<eval-id>/{output_items.jsonl, report.md, scores.csv}`; aggregate scores mirrored to each `agent-metadata.yaml.lastEval.aggregateScores`. Key finding: TourMatchingAgent strongest (100% relevance, 88% task_adherence); FlightBookingAgent weakest but largely because built-in `task_adherence`/`intent_resolution` judges misread correct slot-filling behavior as "off-scope". Custom `behavioral_adherence` is the more accurate signal for our agent contracts. See [Evaluation scaffolding](#evaluation-scaffolding).
- **2026-07-03** — Added [docs/travel-agency.md](docs/travel-agency.md) (this file) consolidating repo memory notes #1–#41 into a human-readable reference.
- **2026-07-03** — Eval scaffolding landed for 4 specialists (FlightBooking, HolidayPackage, CruiseDiscovery, TourMatching) co-located with each agent folder; shared `behavioral_adherence` evaluator under `_shared/evaluators/`; `.foundry/` gitignored; loader now skips YAML without `kind:`. See [Evaluation scaffolding](#evaluation-scaffolding).
- **Earlier (pre-doc):** TripPlannerAgent (hosted) deployed via `azd deploy TripPlannerAgent`, wired into `ROUTE_TO_AGENT["TRIP_PLANNER"]`; all 11 prompt agents pulled to disk via `dump_prompt_agents.py`; portal-metadata filter added to loader; `discover_definitions` filters non-prompt kinds by default. See [Prompt-agent lifecycle](#prompt-agent-lifecycle-source-of-truth-on-disk) and [Foundry project & agent inventory](#foundry-project--agent-inventory).
