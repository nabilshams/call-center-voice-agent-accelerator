import asyncio
import io
import logging
import os
import tempfile
import time
from pathlib import Path

from pydub import AudioSegment
from app.api.foundry_admin import BLUEPRINTS as FOUNDRY_ADMIN_BLUEPRINTS
from app.api.travel_agency import BLUEPRINTS as TRAVEL_AGENCY_BLUEPRINTS
from app.foundry import FoundryAgentManagementError, FoundryAgentManager
from app.handler.acs_event_handler import AcsEventHandler
from app.handler.acs_media_handler import ACSMediaHandler
from app.handler import attachment_extractor
from app.handler.attachment_extractor import (
    AttachmentExtractionError,
    AttachmentTooLargeError,
    UnsupportedAttachmentError,
)
from app.handler.attachment_store import AttachmentStore
from app.handler.demo_script_storage import DemoScriptStorage
from app.handler.foundry_workflow_client import FoundryWorkflowClient, FoundryWorkflowError
from app.handler.local_maf_orchestrator import FoundryAgentError, MAFTravelOrchestrator
from app.handler.speech_transcription_handler import SpeechTranscriptionHandler
from dotenv import load_dotenv
from quart import Quart, request, websocket, jsonify

load_dotenv()

app = Quart(__name__)

# Travel-agency demo APIs (in-memory data, no real backend). Each domain is a
# standalone API with its own OpenAPI spec at {prefix}/openapi.yaml.
for _bp in TRAVEL_AGENCY_BLUEPRINTS:
    app.register_blueprint(_bp)

app.config["AZURE_VOICE_LIVE_API_KEY"] = os.getenv("AZURE_VOICE_LIVE_API_KEY", "")
app.config["AZURE_VOICE_LIVE_ENDPOINT"] = os.getenv("AZURE_VOICE_LIVE_ENDPOINT")
app.config["VOICE_LIVE_MODEL"] = os.getenv("VOICE_LIVE_MODEL", "gpt-4o-mini")
app.config["ACS_CONNECTION_STRING"] = os.getenv("ACS_CONNECTION_STRING")
app.config["ACS_DEV_TUNNEL"] = os.getenv("ACS_DEV_TUNNEL", "")
app.config["AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID"] = os.getenv(
    "AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID", ""
)

# Ambient Scenes Configuration
# Options: none, office, call_center (or custom presets)
app.config["AMBIENT_PRESET"] = os.getenv("AMBIENT_PRESET", "none")

# Voice Live Transport Configuration
# Options: websocket (default, original), webrtc (new, lower latency)
app.config["VOICE_LIVE_TRANSPORT"] = os.getenv("VOICE_LIVE_TRANSPORT", "websocket")

# Speech Transcription Configuration (separate from Voice Live)
app.config["AZURE_SPEECH_KEY"] = os.getenv("AZURE_SPEECH_KEY", "")
app.config["AZURE_SPEECH_REGION"] = os.getenv("AZURE_SPEECH_REGION", "")
app.config["AZURE_SPEECH_ENDPOINT"] = os.getenv("AZURE_SPEECH_ENDPOINT", "")
app.config["AZURE_SPEECH_RESOURCE_ID"] = os.getenv("AZURE_SPEECH_RESOURCE_ID", "")

# Azure OpenAI (used for server-side LLM calls such as live persona inference)
app.config["AZURE_OPENAI_ENDPOINT"] = os.getenv(
    "AZURE_OPENAI_ENDPOINT", os.getenv("AZURE_VOICE_LIVE_ENDPOINT", "")
)
app.config["AZURE_OPENAI_CHAT_DEPLOYMENT"] = os.getenv(
    "AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini"
)

# Travel orchestration configuration
# TRAVEL_ORCHESTRATOR_MODE options:
# - foundry: call the workflow defined in Azure AI Foundry via its endpoint
# - maf: in-process Microsoft Agent Framework orchestrator that calls the
#         agents defined in Azure AI Foundry
app.config["TRAVEL_ORCHESTRATOR_MODE"] = os.getenv("TRAVEL_ORCHESTRATOR_MODE", "maf")
app.config["FOUNDRY_WORKFLOW_ENDPOINT"] = os.getenv("FOUNDRY_WORKFLOW_ENDPOINT", "")
app.config["FOUNDRY_WORKFLOW_PATH"] = os.getenv("FOUNDRY_WORKFLOW_PATH", "")
app.config["FOUNDRY_API_KEY"] = os.getenv("FOUNDRY_API_KEY", "")
app.config["FOUNDRY_WORKFLOW_TIMEOUT_SECONDS"] = os.getenv(
    "FOUNDRY_WORKFLOW_TIMEOUT_SECONDS", "25"
)
app.config["MAF_NATIVE_SDK_ENABLED"] = os.getenv("MAF_NATIVE_SDK_ENABLED", "true")
app.config["MAF_PROJECT_ENDPOINT"] = os.getenv("MAF_PROJECT_ENDPOINT", "")
app.config["MAF_MODEL"] = os.getenv("MAF_MODEL", app.config["AZURE_OPENAI_CHAT_DEPLOYMENT"])

# Upload size cap for /travel/attachments (and /transcription/upload). Default
# 25 MB matches AttachmentStore's per-session cap; override with MAX_UPLOAD_BYTES.
app.config["MAX_CONTENT_LENGTH"] = int(
    os.getenv("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))
)

# Generic Foundry admin HTTP surface (hosted-agent CRUD at /api/foundry/agents).
# Disabled by default; when enabled every request must present ADMIN_API_KEY as
# the X-Admin-Key header. This is a domain-agnostic building block -- consumers
# (travel-agency, etc.) can also import FoundryAgentManager directly.
app.config["FOUNDRY_ADMIN_ENABLED"] = os.getenv("FOUNDRY_ADMIN_ENABLED", "false")
app.config["ADMIN_API_KEY"] = os.getenv("ADMIN_API_KEY", "")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

TRAVEL_PROMPT_FILE = Path(__file__).parent / "prompts" / "travel_booking_prompt.txt"

# Log ambient configuration on startup
ambient_preset = app.config["AMBIENT_PRESET"]
if ambient_preset and ambient_preset != "none":
    logger.info(f"Ambient scenes ENABLED: preset='{ambient_preset}'")
else:
    logger.info("Ambient scenes DISABLED (preset=none)")

acs_handler = AcsEventHandler(app.config)

# In-process Microsoft Agent Framework orchestrator that calls the agents
# defined in Azure AI Foundry (TRAVEL_ORCHESTRATOR_MODE=maf).
local_maf_orchestrator = MAFTravelOrchestrator(app.config)
# Client for the workflow defined in Azure AI Foundry (TRAVEL_ORCHESTRATOR_MODE=foundry).
foundry_workflow_client = FoundryWorkflowClient(app.config)
demo_script_storage = DemoScriptStorage(os.getenv("DEMO_SCRIPT_PATH"))

# Session-scoped store for files uploaded via POST /travel/attachments. Only
# TripPlannerAgent currently consumes them (see AGENTS_ACCEPTING_ATTACHMENTS
# in local_maf_orchestrator.py); records TTL out after 30 minutes of inactivity.
attachment_store = AttachmentStore()

# Optional generic Foundry admin surface. Registered only when explicitly
# enabled AND an admin key is set; keeps the surface off by default in dev/prod.
_foundry_admin_enabled = str(app.config.get("FOUNDRY_ADMIN_ENABLED", "false")).lower() == "true"
if _foundry_admin_enabled:
    if not app.config.get("ADMIN_API_KEY"):
        logger.warning(
            "FOUNDRY_ADMIN_ENABLED=true but ADMIN_API_KEY is empty; "
            "skipping /api/foundry/agents registration."
        )
    elif not app.config.get("MAF_PROJECT_ENDPOINT"):
        logger.warning(
            "FOUNDRY_ADMIN_ENABLED=true but MAF_PROJECT_ENDPOINT is empty; "
            "skipping /api/foundry/agents registration."
        )
    else:
        app.config["FOUNDRY_AGENT_MANAGER"] = FoundryAgentManager(
            app.config["MAF_PROJECT_ENDPOINT"],
            managed_identity_client_id=app.config.get(
                "AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID"
            )
            or None,
        )
        for _bp in FOUNDRY_ADMIN_BLUEPRINTS:
            app.register_blueprint(_bp)
        logger.info("Foundry admin API registered at /api/foundry/agents")


@app.route("/acs/incomingcall", methods=["POST"])
async def incoming_call_handler():
    """Handles initial incoming call event from EventGrid."""
    events = await request.get_json()
    host_url = request.host_url.replace("http://", "https://", 1).rstrip("/")
    return await acs_handler.process_incoming_call(events, host_url, app.config)


@app.route("/acs/callbacks/<context_id>", methods=["POST"])
async def acs_event_callbacks(context_id):
    """Handles ACS event callbacks for call connection and streaming events."""
    raw_events = await request.get_json()
    return await acs_handler.process_callback_events(context_id, raw_events, app.config)


@app.websocket("/acs/ws")
async def acs_ws():
    """WebSocket endpoint for ACS to send audio to Voice Live."""
    logger = logging.getLogger("acs_ws")
    logger.info("Incoming ACS WebSocket connection")
    handler = ACSMediaHandler(app.config)
    await handler.init_incoming_websocket(websocket, is_raw_audio=False)
    connect_task = asyncio.create_task(handler.connect())
    connect_task.add_done_callback(lambda t: t.exception() and logger.error(f"Voice Live connect failed: {t.exception()}") if not t.cancelled() else None)
    try:
        while True:
            msg = await websocket.receive()
            await handler.acs_to_voicelive(msg)
    except asyncio.CancelledError:
        logger.info("ACS WebSocket cancelled")
    except Exception:
        logger.exception("ACS WebSocket connection closed")
    finally:
        await handler.stop_audio_output()


@app.websocket("/web/ws")
async def web_ws():
    """WebSocket endpoint for web clients to send audio to Voice Live."""
    logger = logging.getLogger("web_ws")
    logger.info("Incoming Web WebSocket connection")
    transport_type = app.config.get("VOICE_LIVE_TRANSPORT", "websocket")
    handler = ACSMediaHandler(app.config, transport_type=transport_type)
    await handler.init_incoming_websocket(websocket, is_raw_audio=True)
    connect_task = asyncio.create_task(handler.connect())
    connect_task.add_done_callback(lambda t: t.exception() and logger.error(f"Voice Live connect failed: {t.exception()}") if not t.cancelled() else None)
    try:
        while True:
            msg = await websocket.receive()
            await handler.web_to_voicelive(msg)
    except asyncio.CancelledError:
        logger.info("Web WebSocket cancelled")
    except Exception:
        logger.exception("Web WebSocket connection closed")
    finally:
        await handler.stop_audio_output()


@app.websocket("/webrtc/ws")
async def webrtc_ws():
    """WebSocket endpoint for web clients using WebRTC transport to Voice Live."""
    logger = logging.getLogger("webrtc_ws")
    logger.info("Incoming WebRTC WebSocket connection")
    handler = ACSMediaHandler(app.config, transport_type="webrtc")
    await handler.init_incoming_websocket(websocket, is_raw_audio=True)
    connect_task = asyncio.create_task(handler.connect())
    connect_task.add_done_callback(lambda t: t.exception() and logger.error(f"Voice Live connect failed: {t.exception()}") if not t.cancelled() else None)
    try:
        while True:
            msg = await websocket.receive()
            await handler.web_to_voicelive(msg)
    except asyncio.CancelledError:
        logger.info("WebRTC WebSocket cancelled")
    except Exception:
        logger.exception("WebRTC WebSocket connection closed")
    finally:
        await handler.stop_audio_output()


@app.websocket("/web/travel/ws")
async def web_travel_ws():
    """WebSocket endpoint for travel booking assistant using WebSocket transport."""
    logger = logging.getLogger("web_travel_ws")
    logger.info("Incoming Travel WebSocket connection")
    transport_type = app.config.get("VOICE_LIVE_TRANSPORT", "websocket")
    handler = ACSMediaHandler(
        app.config,
        transport_type=transport_type,
        prompt_file=TRAVEL_PROMPT_FILE,
        storage_type="travel_support_agent",
        persona_context="travel",
        enable_specialists=True,
    )
    await handler.init_incoming_websocket(websocket, is_raw_audio=True)
    connect_task = asyncio.create_task(handler.connect())
    connect_task.add_done_callback(lambda t: t.exception() and logger.error(f"Voice Live connect failed: {t.exception()}") if not t.cancelled() else None)
    try:
        while True:
            msg = await websocket.receive()
            await handler.web_to_voicelive(msg)
    except asyncio.CancelledError:
        logger.info("Travel WebSocket cancelled")
    except Exception:
        logger.exception("Travel WebSocket connection closed")
    finally:
        await handler.stop_audio_output()


@app.websocket("/webrtc/travel/ws")
async def webrtc_travel_ws():
    """WebSocket endpoint for travel booking assistant using WebRTC transport."""
    logger = logging.getLogger("webrtc_travel_ws")
    logger.info("Incoming Travel WebRTC WebSocket connection")
    handler = ACSMediaHandler(
        app.config,
        transport_type="webrtc",
        prompt_file=TRAVEL_PROMPT_FILE,
        storage_type="travel_support_agent",
        persona_context="travel",
        enable_specialists=True,
    )
    await handler.init_incoming_websocket(websocket, is_raw_audio=True)
    connect_task = asyncio.create_task(handler.connect())
    connect_task.add_done_callback(lambda t: t.exception() and logger.error(f"Voice Live connect failed: {t.exception()}") if not t.cancelled() else None)
    try:
        while True:
            msg = await websocket.receive()
            await handler.web_to_voicelive(msg)
    except asyncio.CancelledError:
        logger.info("Travel WebRTC WebSocket cancelled")
    except Exception:
        logger.exception("Travel WebRTC WebSocket connection closed")
    finally:
        await handler.stop_audio_output()


@app.route("/")
async def index():
    """Serves the static index page."""
    return await app.send_static_file("index.html")


@app.route("/travel-support")
async def travel_support_page():
    """Serves the travel booking and hotel reservations support UI page."""
    return await app.send_static_file("travel-support.html")


@app.route("/travel-chat")
async def travel_chat_page():
    """Serves the travel support chat UI page."""
    return await app.send_static_file("travel-chat.html")


@app.route("/travel/demo-script", methods=["GET"])
async def get_demo_script():
    """Returns the saved (or seeded) travel demo script."""
    return jsonify(demo_script_storage.load())


@app.route("/travel/demo-script", methods=["PUT", "POST"])
async def save_demo_script():
    """Persists an edited travel demo script."""
    payload = await request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "body must be a JSON object with a 'sections' array"}), 400
    saved = demo_script_storage.save(payload)
    logger.info("Demo script saved (sections=%s)", len(saved.get("sections", [])))
    return jsonify(saved)


@app.route("/travel/demo-script/reset", methods=["POST"])
async def reset_demo_script():
    """Restores the demo script to the version shipped in the codebase."""
    restored = demo_script_storage.reset()
    logger.info("Demo script reset to seed (sections=%s)", len(restored.get("sections", [])))
    return jsonify(restored)


@app.route("/travel/attachments", methods=["POST"])
async def travel_attachment_upload():
    """Upload a file the customer wants TripPlannerAgent to see.

    Multipart form:
      - ``file``       (required): the file itself.
      - ``session_id`` (required): opaque client-minted id; groups a
        conversation's attachments and drives TTL.

    Returns ``{attachment_id, filename, kind, mime, size_bytes, has_text}``
    on 201; ``{error}`` with 4xx on validation problems. The extracted text
    is intentionally NOT returned to the browser -- callers reference the
    file by id in a subsequent ``/travel/orchestrate`` request.
    """
    logger = logging.getLogger("travel_attachment_upload")
    try:
        form = await request.form
        files = await request.files
    except Exception as exc:
        logger.warning("multipart_parse_failed error=%s", exc)
        return jsonify({"error": "Malformed multipart body."}), 400

    session_id = (form.get("session_id") or "").strip()
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    upload = files.get("file")
    if upload is None or not (upload.filename or "").strip():
        return jsonify({"error": "file is required"}), 400

    filename = upload.filename
    mime = (upload.mimetype or "").strip() or "application/octet-stream"

    raw_bytes = upload.read()
    if not raw_bytes:
        return jsonify({"error": "Empty file."}), 400

    try:
        extracted = await attachment_extractor.extract(
            raw_bytes=raw_bytes, mime=mime, filename=filename,
        )
    except UnsupportedAttachmentError as exc:
        logger.info(
            "attachment_rejected_mime filename=%s mime=%s reason=%s",
            filename, mime, exc,
        )
        return jsonify({"error": str(exc)}), 415
    except AttachmentTooLargeError as exc:
        logger.info("attachment_rejected_size filename=%s size=%s", filename, len(raw_bytes))
        return jsonify({"error": str(exc)}), 413
    except AttachmentExtractionError as exc:
        logger.warning("attachment_extract_failed filename=%s error=%s", filename, exc)
        return jsonify({"error": str(exc)}), 422

    try:
        record = attachment_store.add(
            session_id=session_id,
            filename=filename,
            kind=extracted.kind,
            mime=extracted.mime,
            size_bytes=len(raw_bytes),
            extracted_text=extracted.extracted_text,
        )
    except ValueError as exc:
        # Per-session limits (count or total bytes) exceeded.
        return jsonify({"error": str(exc)}), 409

    logger.info(
        "attachment_uploaded session_id=%s attachment_id=%s kind=%s size=%s",
        session_id, record.attachment_id, extracted.kind, len(raw_bytes),
    )
    return jsonify({"session_id": session_id, **record.to_public_dict()}), 201


@app.route("/travel/attachments", methods=["GET"])
async def travel_attachment_list():
    """List attachments currently held for a session."""
    session_id = (request.args.get("session_id") or "").strip()
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400
    records = attachment_store.list_session(session_id)
    return jsonify(
        {
            "session_id": session_id,
            "attachments": [r.to_public_dict() for r in records],
        }
    )


@app.route("/travel/attachments/<attachment_id>", methods=["DELETE"])
async def travel_attachment_delete(attachment_id: str):
    """Remove a single attachment from a session (used when the user clicks ×)."""
    session_id = (request.args.get("session_id") or "").strip()
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400
    removed = attachment_store.delete(session_id, attachment_id)
    if not removed:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"deleted": attachment_id, "session_id": session_id})


@app.route("/travel/orchestrate", methods=["POST"])
async def travel_orchestrate():
    """Routes travel requests to the Foundry workflow endpoint (foundry) or the
    in-process MAF orchestrator that calls Foundry agents (maf)."""
    def emit_orchestration_metric(
        *,
        outcome: str,
        orchestrator_mode: str,
        selected_agents: list[str] | None,
        confidence: float,
        latency_ms: int,
        fallback_reason: str = "",
    ):
        # Compact key-value metric log, easy to query in Application Insights.
        logger.info(
            "travel_orchestration_metric outcome=%s orchestrator_mode=%s selected_agents=%s confidence=%.3f latency_ms=%s fallback_reason=%s",
            outcome,
            orchestrator_mode,
            len(selected_agents or []),
            confidence,
            latency_ms,
            fallback_reason or "none",
        )

    started_at = time.perf_counter()
    payload = await request.get_json() or {}
    message = (payload.get("message") or "").strip()
    context = payload.get("context") or {}

    # Attachment plumbing: the client mints a session_id per browser session
    # and includes attachment_ids returned by /travel/attachments. Both are
    # optional; when absent the orchestrator behaves exactly as before.
    session_id = (payload.get("session_id") or "").strip()
    raw_attachment_ids = payload.get("attachment_ids") or []
    attachment_ids: list[str] = [
        str(aid).strip()
        for aid in raw_attachment_ids
        if isinstance(aid, (str, int)) and str(aid).strip()
    ] if isinstance(raw_attachment_ids, list) else []

    if not isinstance(context, dict):
        emit_orchestration_metric(
            outcome="bad_request",
            orchestrator_mode="none",
            selected_agents=[],
            confidence=0.0,
            latency_ms=int((time.perf_counter() - started_at) * 1000),
            fallback_reason="invalid_context_type",
        )
        return jsonify({"error": "context must be a JSON object"}), 400

    if not message:
        emit_orchestration_metric(
            outcome="clarification",
            orchestrator_mode="none",
            selected_agents=[],
            confidence=0.2,
            latency_ms=int((time.perf_counter() - started_at) * 1000),
            fallback_reason="empty_message",
        )
        return jsonify(
            {
                "spoken_reply": "Please tell me what you need help with for your trip.",
                "clarification_question": "Would you like help with flights, hotels, or both?",
                "selected_agents": [],
                "specialist_outputs": [],
                "confidence": 0.2,
                "next_step": "collect_intent",
            }
        )

    mode = app.config.get("TRAVEL_ORCHESTRATOR_MODE", "foundry").lower()
    logger.info("Travel orchestrator request received (mode=%s)", mode)

    # MAF mode: in-process orchestrator that calls the agents defined in Foundry.
    if mode == "maf":
        strategy = (payload.get("strategy") or context.get("orchestration") or "single").lower()

        # Resolve attachment ids -> lightweight dicts the orchestrator can
        # prepend to the TripPlannerAgent prompt. Unknown / evicted ids are
        # silently skipped by the store (logged as warnings) so a stale
        # client-side id never blocks a turn.
        attachments_for_orchestrator: list[dict] = []
        if session_id and attachment_ids:
            records = attachment_store.get_many(session_id, attachment_ids)
            attachments_for_orchestrator = [
                {
                    "attachment_id": r.attachment_id,
                    "filename": r.filename,
                    "kind": r.kind,
                    "text": r.extracted_text,
                }
                for r in records
            ]
            logger.info(
                "orchestrate_attachments_resolved session_id=%s requested=%s resolved=%s",
                session_id, len(attachment_ids), len(attachments_for_orchestrator),
            )

        try:
            if strategy in ("multi", "multi-intent", "parallel"):
                result = await local_maf_orchestrator.orchestrate_multi(
                    message=message,
                    context=context,
                    attachments=attachments_for_orchestrator or None,
                )
            else:
                result = await local_maf_orchestrator.orchestrate(
                    message=message,
                    context=context,
                    attachments=attachments_for_orchestrator or None,
                )
        except FoundryAgentError as exc:
            logger.error("MAF orchestration failed: %s", exc)
            emit_orchestration_metric(
                outcome="error",
                orchestrator_mode="maf",
                selected_agents=[],
                confidence=0.0,
                latency_ms=int((time.perf_counter() - started_at) * 1000),
                fallback_reason="maf_failed",
            )
            return jsonify({"error": f"MAF orchestration failed: {exc}"}), 502
        result["orchestrator_mode"] = "maf"
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        emit_orchestration_metric(
            outcome="success",
            orchestrator_mode=result.get("orchestrator_mode", "maf"),
            selected_agents=result.get("selected_agents", []),
            confidence=float(result.get("confidence", 0.0)),
            latency_ms=latency_ms,
        )
        logger.info(
            "Travel orchestrator response (mode=%s, agents=%s, confidence=%s)",
            result.get("orchestrator_mode"),
            result.get("selected_agents", []),
            result.get("confidence", 0.0),
        )
        return jsonify(result)

    # Foundry mode: call the workflow defined in Azure AI Foundry via its endpoint.
    if mode == "foundry":
        if not foundry_workflow_client.is_configured():
            emit_orchestration_metric(
                outcome="error",
                orchestrator_mode="foundry",
                selected_agents=[],
                confidence=0.0,
                latency_ms=int((time.perf_counter() - started_at) * 1000),
                fallback_reason="foundry_not_configured",
            )
            return jsonify({"error": "Foundry workflow is not configured"}), 503
        try:
            result = await foundry_workflow_client.invoke(message=message, context=context)
        except FoundryWorkflowError as exc:
            logger.error("Foundry workflow failed: %s", exc)
            emit_orchestration_metric(
                outcome="error",
                orchestrator_mode="foundry",
                selected_agents=[],
                confidence=0.0,
                latency_ms=int((time.perf_counter() - started_at) * 1000),
                fallback_reason="foundry_failed",
            )
            return jsonify({"error": f"Foundry workflow failed: {exc}"}), 502
        result["orchestrator_mode"] = "foundry"
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        emit_orchestration_metric(
            outcome="success",
            orchestrator_mode=result.get("orchestrator_mode", "foundry"),
            selected_agents=result.get("selected_agents", []),
            confidence=float(result.get("confidence", 0.0)),
            latency_ms=latency_ms,
        )
        logger.info(
            "Travel orchestrator response (mode=%s, agents=%s, confidence=%s)",
            result.get("orchestrator_mode"),
            result.get("selected_agents", []),
            result.get("confidence", 0.0),
        )
        return jsonify(result)

    emit_orchestration_metric(
        outcome="bad_request",
        orchestrator_mode=mode,
        selected_agents=[],
        confidence=0.0,
        latency_ms=int((time.perf_counter() - started_at) * 1000),
        fallback_reason="unknown_mode",
    )
    return jsonify(
        {"error": f"Unknown TRAVEL_ORCHESTRATOR_MODE '{mode}'; expected 'foundry' or 'maf'"}
    ), 400


# ============================================================================
# TRANSCRIPTION FEATURE (Separate from Voice Agent)
# ============================================================================

@app.route("/live-transcription")
async def live_transcription_page():
    """Serves the live transcription UI page."""
    return await app.send_static_file("live-transcription.html")


@app.route("/recap")
async def recap_page():
    """Serves the Recap UI page (clinician-focused fork of live transcription)."""
    return await app.send_static_file("recap.html")


@app.route("/transcription")
async def transcription_page():
    """Serves the transcription UI page."""
    return await app.send_static_file("transcription.html")


@app.route("/transcription/upload", methods=["POST"])
async def transcription_upload():
    """Handle audio file upload and transcribe using Azure Speech SDK with speaker diarization."""
    import azure.cognitiveservices.speech as speechsdk
    from azure.identity import ManagedIdentityCredential
    import threading
    import wave
    
    logger = logging.getLogger("transcription_upload")
    
    try:
        files = await request.files
        form = await request.form
        
        if 'audio' not in files:
            return jsonify({"error": "No audio file provided"}), 400
        
        audio_file = files['audio']
        audio_data = audio_file.read()
        enable_diarization = form.get('enableDiarization', 'true').lower() == 'true'
        original_filename = audio_file.filename or "audio.wav"
        file_extension = Path(original_filename).suffix.lower()
        
        # Validate file extension before processing
        SUPPORTED_AUDIO_EXTENSIONS = {'.wav', '.mp3', '.ogg', '.flac', '.m4a', '.mp4', '.aac'}
        if file_extension not in SUPPORTED_AUDIO_EXTENSIONS:
            logger.warning(f"Unsupported file type uploaded: {file_extension}")
            return jsonify({
                "error": f"Unsupported file type: {file_extension}. Please upload an audio file in one of the supported formats: WAV, MP3, OGG, FLAC, M4A."
            }), 400
        
        logger.info(f"Received audio file: {original_filename}, size: {len(audio_data)} bytes, format: {file_extension}, diarization: {enable_diarization}")
        
        # Save original file to temp location first
        with tempfile.NamedTemporaryFile(suffix=file_extension, delete=False) as tmp_original:
            tmp_original.write(audio_data)
            tmp_original_path = tmp_original.name
        
        # Convert to WAV format for Speech SDK compatibility
        tmp_path = None
        try:
            logger.info(f"Converting {file_extension} to WAV format...")
            
            # Load audio with pydub (handles mp3, ogg, flac, m4a, wav, etc.)
            if file_extension in ['.mp3']:
                audio = AudioSegment.from_mp3(tmp_original_path)
            elif file_extension in ['.ogg']:
                audio = AudioSegment.from_ogg(tmp_original_path)
            elif file_extension in ['.flac']:
                audio = AudioSegment.from_file(tmp_original_path, format='flac')
            elif file_extension in ['.m4a', '.mp4', '.aac']:
                audio = AudioSegment.from_file(tmp_original_path, format='m4a')
            elif file_extension in ['.wav']:
                audio = AudioSegment.from_wav(tmp_original_path)
            else:
                # Try generic loading
                audio = AudioSegment.from_file(tmp_original_path)
            
            # Get duration from pydub (in milliseconds)
            audio_duration_seconds = len(audio) / 1000.0
            logger.info(f"Audio duration: {audio_duration_seconds:.2f} seconds")
            
            # Convert to format required by Azure Speech SDK ConversationTranscriber:
            # - 16kHz sample rate (recommended for speech recognition)
            # - Mono (1 channel) - required for diarization
            # - 16-bit sample width (2 bytes)
            audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
            
            # Export to PCM WAV format (uncompressed)
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_wav:
                tmp_path = tmp_wav.name
            
            # Export with explicit PCM parameters for Speech SDK compatibility
            audio.export(
                tmp_path, 
                format='wav',
                parameters=["-acodec", "pcm_s16le"]  # PCM signed 16-bit little-endian
            )
            logger.info(f"Converted to WAV (16kHz, mono, 16-bit PCM): {tmp_path}")
            
            # Verify the WAV file format
            with wave.open(tmp_path, 'rb') as wav_check:
                channels = wav_check.getnchannels()
                sample_width = wav_check.getsampwidth()
                frame_rate = wav_check.getframerate()
                logger.info(f"WAV verification - channels: {channels}, sample_width: {sample_width}, frame_rate: {frame_rate}")
                
                if channels != 1:
                    raise ValueError(f"WAV must be mono, got {channels} channels")
                if sample_width != 2:
                    raise ValueError(f"WAV must be 16-bit (2 bytes), got {sample_width} bytes")
            
        except Exception as convert_err:
            logger.error(f"Audio conversion failed: {convert_err}")
            # Clean up temp files
            if os.path.exists(tmp_original_path):
                os.unlink(tmp_original_path)
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
            return jsonify({"error": f"Failed to convert audio file: {str(convert_err)}. Please upload a valid audio file (WAV, MP3, OGG, FLAC, M4A)."}), 400
        finally:
            # Clean up original temp file
            if os.path.exists(tmp_original_path):
                os.unlink(tmp_original_path)
        
        try:
            # Create speech config
            speech_key = app.config.get("AZURE_SPEECH_KEY", "")
            speech_region = app.config.get("AZURE_SPEECH_REGION", "")
            speech_resource_id = app.config.get("AZURE_SPEECH_RESOURCE_ID", "")
            speech_endpoint = app.config.get("AZURE_SPEECH_ENDPOINT", "")
            client_id = app.config.get("AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID", "")
            
            if speech_key and speech_region:
                # Use API key authentication
                speech_config = speechsdk.SpeechConfig(
                    subscription=speech_key,
                    region=speech_region
                )
            elif speech_resource_id:
                # Use Azure AD authentication with custom endpoint
                from azure.identity import AzureCliCredential, DefaultAzureCredential
                logger.info("Using Azure AD authentication...")
                logger.info(f"Speech resource ID: {speech_resource_id}")
                logger.info(f"Speech region: {speech_region}")
                
                try:
                    # Prefer Azure CLI credential for local development
                    credential = AzureCliCredential()
                    token = credential.get_token("https://cognitiveservices.azure.com/.default")
                    logger.info("Successfully obtained token via Azure CLI")
                except Exception as cli_err:
                    logger.warning(f"Azure CLI credential failed: {cli_err}, falling back to DefaultAzureCredential")
                    credential = DefaultAzureCredential(managed_identity_client_id=client_id if client_id else None)
                    token = credential.get_token("https://cognitiveservices.azure.com/.default")
                
                # For conversation transcription, we need to use region-based config
                # Custom endpoint doesn't work well with ConversationTranscriber
                if speech_region:
                    logger.info(f"Creating SpeechConfig with region: {speech_region}")
                    speech_config = speechsdk.SpeechConfig(subscription="placeholder", region=speech_region)
                    # Override with AAD token
                    auth_token = f"aad#{speech_resource_id}#{token.token}"
                    speech_config.authorization_token = auth_token
                    logger.info(f"Set authorization token for AAD auth")
                elif speech_endpoint:
                    # Fallback to custom endpoint
                    endpoint_url = speech_endpoint.rstrip('/')
                    logger.info(f"Using custom endpoint: {endpoint_url}")
                    speech_config = speechsdk.SpeechConfig(endpoint=endpoint_url)
                    auth_token = f"aad#{speech_resource_id}#{token.token}"
                    speech_config.authorization_token = auth_token
                else:
                    return jsonify({"error": "AZURE_SPEECH_REGION is required for transcription"}), 500
            elif client_id and speech_region:
                # Use managed identity (legacy path)
                credential = ManagedIdentityCredential(client_id=client_id)
                token = credential.get_token("https://cognitiveservices.azure.com/.default")
                host_url = f"wss://{speech_region}.stt.speech.microsoft.com"
                speech_config = speechsdk.SpeechConfig(host=host_url)
                if speech_resource_id:
                    auth_token = f"aad#{speech_resource_id}#{token.token}"
                else:
                    auth_token = token.token
                speech_config.authorization_token = auth_token
            else:
                return jsonify({"error": "Azure Speech credentials not configured. Set AZURE_SPEECH_KEY + AZURE_SPEECH_REGION, or AZURE_SPEECH_REGION + AZURE_SPEECH_RESOURCE_ID for AAD auth"}), 500
            
            speech_config.speech_recognition_language = "en-US"
            
            # Create audio config with explicit format for Speech SDK compatibility
            # Use push stream for better control over audio format
            audio_format = speechsdk.audio.AudioStreamFormat(
                samples_per_second=16000,
                bits_per_sample=16,
                channels=1
            )
            
            # Read WAV file data (skip header) for push stream
            with wave.open(tmp_path, 'rb') as wav_file:
                wav_frames = wav_file.getnframes()
                wav_data = wav_file.readframes(wav_frames)
            
            # Create push stream and push all audio data
            push_stream = speechsdk.audio.PushAudioInputStream(stream_format=audio_format)
            push_stream.write(wav_data)
            push_stream.close()
            
            audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
            logger.info(f"Created audio config with push stream: {len(wav_data)} bytes of audio data")
            
            # Calculate estimated cost (Azure Speech batch transcription: $0.18/hour = $0.003/minute)
            cost_per_minute = 0.003
            estimated_cost = (audio_duration_seconds / 60) * cost_per_minute
            
            transcription_segments = []
            done_event = threading.Event()
            error_message = None
            
            if enable_diarization:
                # Use ConversationTranscriber for speaker diarization
                logger.info("Using conversation transcriber with speaker diarization...")
                
                conversation_transcriber = speechsdk.transcription.ConversationTranscriber(
                    speech_config=speech_config,
                    audio_config=audio_config
                )
                
                def on_transcribing(evt):
                    logger.debug(f"Transcribing: {evt.result.text}")
                
                def on_transcribed(evt):
                    logger.info(f"Transcribed event received, reason: {evt.result.reason}")
                    if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                        speaker_id = evt.result.speaker_id if hasattr(evt.result, 'speaker_id') else "Unknown"
                        offset_ticks = evt.result.offset if hasattr(evt.result, 'offset') else 0
                        # Convert ticks (100-nanosecond units) to seconds
                        offset_seconds = offset_ticks / 10000000
                        
                        segment = {
                            "speaker": speaker_id,
                            "text": evt.result.text,
                            "offset": offset_seconds
                        }
                        transcription_segments.append(segment)
                        logger.info(f"[{speaker_id}] ({offset_seconds:.2f}s): {evt.result.text}")
                    elif evt.result.reason == speechsdk.ResultReason.NoMatch:
                        logger.warning(f"No match - no speech detected in segment")
                
                def on_canceled(evt):
                    nonlocal error_message
                    logger.info(f"Transcription canceled: {evt.reason}")
                    if evt.reason == speechsdk.CancellationReason.Error:
                        error_message = evt.error_details
                        logger.error(f"Error details: {evt.error_details}")
                    done_event.set()
                
                def on_session_started(evt):
                    logger.info("Conversation transcription session started")
                
                def on_session_stopped(evt):
                    logger.info("Session stopped")
                    done_event.set()
                
                conversation_transcriber.transcribing.connect(on_transcribing)
                conversation_transcriber.transcribed.connect(on_transcribed)
                conversation_transcriber.canceled.connect(on_canceled)
                conversation_transcriber.session_started.connect(on_session_started)
                conversation_transcriber.session_stopped.connect(on_session_stopped)
                
                logger.info("Starting conversation transcription...")
                conversation_transcriber.start_transcribing_async().get()
                
                loop = asyncio.get_running_loop()
                try:
                    await asyncio.wait_for(
                        loop.run_in_executor(None, lambda: done_event.wait(timeout=300)),
                        timeout=310
                    )
                except asyncio.TimeoutError:
                    logger.warning("Transcription timed out")
                
                conversation_transcriber.stop_transcribing_async().get()
                
                if error_message:
                    return jsonify({"error": error_message}), 500
                
                # Sort segments by offset
                transcription_segments.sort(key=lambda x: x.get('offset', 0))
                
                # Build full transcription with speaker labels
                full_lines = []
                for seg in transcription_segments:
                    speaker = seg.get('speaker', 'Unknown')
                    # Map guest speakers to friendly names
                    if speaker.startswith('Guest-'):
                        speaker_num = speaker.replace('Guest-', '')
                        speaker = f"Speaker {speaker_num}"
                    full_lines.append(f"[{speaker}]: {seg['text']}")
                
                full_transcription = "\n".join(full_lines)
                
            else:
                # Standard recognition without diarization
                logger.info("Using standard speech recognition (no diarization)...")
                
                speech_recognizer = speechsdk.SpeechRecognizer(
                    speech_config=speech_config,
                    audio_config=audio_config
                )
                
                transcription_parts = []
                
                def on_recognized(evt):
                    if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                        transcription_parts.append(evt.result.text)
                        transcription_segments.append({
                            "speaker": "Speaker",
                            "text": evt.result.text,
                            "offset": evt.result.offset / 10000000 if hasattr(evt.result, 'offset') else 0
                        })
                        logger.info(f"Recognized: {evt.result.text}")
                
                def on_canceled(evt):
                    nonlocal error_message
                    if evt.reason == speechsdk.CancellationReason.Error:
                        error_message = evt.error_details
                        logger.error(f"Error details: {evt.error_details}")
                    done_event.set()
                
                def on_session_stopped(evt):
                    logger.info("Session stopped")
                    done_event.set()
                
                speech_recognizer.recognized.connect(on_recognized)
                speech_recognizer.canceled.connect(on_canceled)
                speech_recognizer.session_stopped.connect(on_session_stopped)
                
                logger.info("Starting continuous recognition...")
                speech_recognizer.start_continuous_recognition()
                
                loop = asyncio.get_running_loop()
                try:
                    await asyncio.wait_for(
                        loop.run_in_executor(None, lambda: done_event.wait(timeout=300)),
                        timeout=310
                    )
                except asyncio.TimeoutError:
                    logger.warning("Transcription timed out")
                
                speech_recognizer.stop_continuous_recognition()
                
                if error_message:
                    return jsonify({"error": error_message}), 500
                
                full_transcription = " ".join(transcription_parts)
            
            # Get unique speakers
            speakers = list(set(seg.get('speaker', 'Unknown') for seg in transcription_segments))
            
            logger.info(f"Transcription complete: {len(transcription_segments)} segments, {len(speakers)} speakers")
            
            return jsonify({
                "transcription": full_transcription,
                "segments": transcription_segments,
                "speakers": speakers,
                "duration_seconds": audio_duration_seconds,
                "estimated_cost_usd": round(estimated_cost, 4)
            })
            
        finally:
            # Clean up temp file - add small delay to ensure file is released
            try:
                await asyncio.sleep(0.1)
                os.unlink(tmp_path)
            except Exception as e:
                logger.warning(f"Could not delete temp file: {e}")
            
    except Exception as e:
        logger.exception("Transcription upload failed")
        return jsonify({"error": str(e)}), 500


@app.websocket("/transcription/ws")
async def transcription_ws():
    """WebSocket endpoint for real-time speech transcription.
    
    This is a standalone transcription feature using Azure Speech SDK,
    completely separate from the Voice Live agent functionality.
    """
    await _run_transcription_ws(mode="default")


@app.websocket("/recap/ws")
async def recap_ws():
    """WebSocket endpoint for the Recap clinical-encounter experience.

    Same audio path as ``/transcription/ws`` but uses the Recap-mode handler
    which biases diarization to Clinician/Patient/Other and produces a
    structured SOAP note instead of a free-text summary.
    """
    await _run_transcription_ws(mode="recap")


async def _run_transcription_ws(mode: str):
    logger = logging.getLogger("transcription_ws")
    logger.info("Incoming transcription WebSocket connection (mode=%s)", mode)

    handler = SpeechTranscriptionHandler(app.config, mode=mode)
    # Stash on app for the section-regenerate endpoint. Connection-id keying
    # is not needed for a demo; we just keep the most recent handler so the
    # current page can call back into it.
    if mode == "recap":
        app.config["RECAP_LAST_HANDLER"] = handler
    await handler.init_websocket(websocket)

    try:
        await handler.start()

        while True:
            msg = await websocket.receive()
            if isinstance(msg, bytes):
                await handler.process_audio(msg)
            elif isinstance(msg, str):
                # Client may send a JSON control frame, e.g. {"type":"stop"} to
                # request a graceful shutdown that produces a final summary
                # before the WebSocket is closed.
                try:
                    import json as _json
                    parsed = _json.loads(msg)
                except (ValueError, TypeError):
                    continue
                if isinstance(parsed, dict) and parsed.get("type") == "stop":
                    logger.info("Transcription stop requested by client")
                    await handler.stop()
                    break

    except asyncio.CancelledError:
        logger.info("Transcription WebSocket cancelled")
    except Exception:
        logger.exception("Transcription WebSocket connection closed")
    finally:
        # Idempotent: if stop already ran (graceful path) this is a no-op.
        await handler.stop()


@app.route("/recap/api/regenerate-section", methods=["POST"])
async def recap_regenerate_section():
    """Regenerate a single SOAP section using the last Recap handler.

    Body: {"section": "subjective"} (one of chief_complaint, subjective,
    objective, assessment, plan).
    Returns: {"section": ..., "text": ..., "confidence": ...} or 4xx.
    """
    from quart import request  # local import to avoid surprises elsewhere
    body = await request.get_json(silent=True) or {}
    section = str(body.get("section") or "").strip()
    if not section:
        return jsonify({"error": "Missing 'section'"}), 400

    handler = app.config.get("RECAP_LAST_HANDLER")
    if handler is None:
        return jsonify({"error": "No active Recap encounter"}), 409

    inference = getattr(handler, "_persona_inference", None)
    gen = getattr(inference, "generate_section", None)
    if not callable(gen):
        return jsonify({"error": "Section regeneration not supported"}), 409

    result = await gen(section)
    if not result:
        return jsonify({"error": "Failed to generate section"}), 502
    return jsonify(result)



if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8000)
