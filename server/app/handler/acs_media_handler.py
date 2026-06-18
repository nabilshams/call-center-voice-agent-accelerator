"""Handles media streaming to Azure Voice Live API via WebSocket or WebRTC."""

import asyncio
import base64
import json
import logging
import uuid
from pathlib import Path
from typing import Optional

import numpy as np
from azure.identity.aio import ManagedIdentityCredential, DefaultAzureCredential
from websockets.typing import Data

from .ambient_mixer import AmbientMixer
from .local_maf_orchestrator import LocalMAFTravelOrchestrator
from .personas import get_random_persona, build_persona_prompt, Persona
from .transport_factory import TransportFactory, TransportType
from .transcription_storage import TranscriptionStorage
from .voice_live_transport import VoiceLiveTransport

logger = logging.getLogger(__name__)

# Default chunk size in bytes (100ms of audio at 24kHz, 16-bit mono)
DEFAULT_CHUNK_SIZE = 4800  # 24000 samples/sec * 0.1 sec * 2 bytes

# Path to system prompt file
SYSTEM_PROMPT_FILE = Path(__file__).parent.parent.parent / "prompts" / "system_prompt.txt"

# Name of the Voice Live function tool used to consult the specialist agents.
SPECIALIST_TOOL_NAME = "consult_travel_specialists"

# Deterministic holding phrase spoken while the specialist agents are queried, so
# the caller hears a natural acknowledgment instead of dead air during orchestration.
SPECIALIST_HOLDING_PHRASE = (
    "Let me check that with our travel specialists — give me just a moment."
)

# Extra guidance appended to the system prompt when specialist tool calling is on.
SPECIALIST_PROMPT_GUIDANCE = (
    "You can consult specialist travel agents (flights, holiday packages, cruises, "
    "tours, inspiration, deals, post-booking, and consultant match). Whenever the "
    f"traveler's request needs specialist knowledge, call the {SPECIALIST_TOOL_NAME} "
    "function with their request. The system will let the traveler know you are "
    "checking with the specialists, so you do not need to stall yourself. When the "
    "specialist results come back, relay them naturally in one cohesive, voice-friendly "
    "reply and never invent prices, availability, or booking references."
)


def _specialist_tool_schema() -> dict:
    """Voice Live function-tool definition for consulting the specialist agents."""
    return {
        "type": "function",
        "name": SPECIALIST_TOOL_NAME,
        "description": (
            "Consult the Wanderlux specialist travel agents (flights, holiday packages, "
            "cruises, tours, inspiration, deals, post-booking concierge, consultant match) "
            "when the traveler's request spans one or more of those areas and their input "
            "would improve the answer. Returns a blended summary plus any single follow-up "
            "question to ask."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": (
                        "The traveler's current request in your own words, capturing "
                        "everything they want help with across all relevant travel areas."
                    ),
                }
            },
            "required": ["request"],
        },
    }


def load_system_prompt(prompt_file: Optional[Path] = None) -> str:
    """Load the system prompt from the external file."""
    file_to_load = prompt_file or SYSTEM_PROMPT_FILE
    try:
        prompt = file_to_load.read_text(encoding="utf-8").strip()
        logger.info(f"[VoiceLiveACSHandler] Loaded system prompt from: {file_to_load}")
        logger.info(f"[VoiceLiveACSHandler] System prompt length: {len(prompt)} chars")
        logger.info(f"[VoiceLiveACSHandler] System prompt preview: {prompt[:200]}...")
        return prompt
    except FileNotFoundError:
        logger.warning(f"System prompt file not found: {file_to_load}, using default")
        return "You are a helpful AI assistant responding in natural, engaging language."
    except Exception as e:
        logger.error(f"Error loading system prompt: {e}, using default")
        return "You are a helpful AI assistant responding in natural, engaging language."


def session_config(
    persona: Persona,
    prompt_file: Optional[Path] = None,
    persona_context: str = "mmh",
    enable_specialists: bool = False,
):
    """Returns the session configuration for Voice Live with the given persona.
    
    Args:
        persona: The persona to use for this session
        prompt_file: Optional system prompt override
        persona_context: Persona pool to draw from (e.g. "mmh" or "travel")
        enable_specialists: When True, register the specialist function tool so the
            model can consult the multi-agent orchestrator during the conversation.
    """
    base_prompt = load_system_prompt(prompt_file)
    personalized_prompt = build_persona_prompt(persona, base_prompt, persona_context)

    if enable_specialists:
        personalized_prompt = f"{personalized_prompt}\n\n{SPECIALIST_PROMPT_GUIDANCE}"

    logger.info(f"[VoiceLiveACSHandler] Session persona: {persona['name']} (voice: {persona['voice']})")
    
    session = {
        "type": "session.update",
        "session": {
            "instructions": personalized_prompt,
            "turn_detection": {
                "type": "azure_semantic_vad",
                "threshold": 0.5,  # Higher = faster detection of speech end
                "prefix_padding_ms": 100,  # Reduced from 200
                "silence_duration_ms": 150,  # Reduced from 200 - faster response after silence
                "remove_filler_words": False,
                "end_of_utterance_detection": {
                    "model": "semantic_detection_v1",
                    "threshold": 0.05,  # Increased from 0.01 - faster end detection
                    "timeout": 1,  # Reduced from 2 seconds
                },
            },
            "input_audio_noise_reduction": {"type": "azure_deep_noise_suppression"},
            "input_audio_echo_cancellation": {"type": "server_echo_cancellation"},
            "voice": {
                "name": persona["voice"],
                "type": "azure-standard",
                "temperature": 0.6,  # Reduced from 0.8 - faster, more predictable responses
            },
        },
    }

    if enable_specialists:
        session["session"]["tools"] = [_specialist_tool_schema()]
        session["session"]["tool_choice"] = "auto"

    return session


class ACSMediaHandler:
    """Manages audio streaming between client and Azure Voice Live API."""

    def __init__(
        self,
        config,
        transport_type: TransportType = "websocket",
        prompt_file: Optional[Path] = None,
        storage_type: str = "cybersecurity_agent",
        persona_context: str = "mmh",
        enable_specialists: bool = False,
    ):
        self.endpoint = config["AZURE_VOICE_LIVE_ENDPOINT"]
        self.model = config["VOICE_LIVE_MODEL"]
        self.api_key = config["AZURE_VOICE_LIVE_API_KEY"]
        self.client_id = config["AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID"]
        self.send_queue = asyncio.Queue()
        self.persona: Optional[Persona] = None  # Selected persona for this session
        self._transport: Optional[VoiceLiveTransport] = None
        self._transport_type: TransportType = transport_type
        self.send_task = None
        self.incoming_websocket = None
        self.is_raw_audio = True
        self._prompt_file = prompt_file
        self._persona_context = persona_context

        # TTS output buffering for continuous ambient mixing
        self._tts_output_buffer = bytearray()
        self._tts_buffer_lock = asyncio.Lock()
        self._max_buffer_size = 480000  # 10 seconds of audio - large enough for long responses
        self._buffer_warning_logged = False
        self._tts_playback_started = False  # Track if we've started playing TTS
        self._min_buffer_to_start = 9600  # 200ms buffer before starting TTS playback
        
        # Ambient mixer initialization
        self._ambient_mixer: Optional[AmbientMixer] = None
        ambient_preset = config.get("AMBIENT_PRESET", "none")
        if ambient_preset and ambient_preset != "none":
            try:
                self._ambient_mixer = AmbientMixer(preset=ambient_preset)
            except Exception as e:
                logger.error(f"Failed to initialize AmbientMixer: {e}")

        self._transcription_storage = TranscriptionStorage(storage_type=storage_type)

        # Multi-agent specialist orchestration (Voice Live function tool).
        self._enable_specialists = enable_specialists
        self._orchestrator: Optional[LocalMAFTravelOrchestrator] = None
        if enable_specialists:
            try:
                self._orchestrator = LocalMAFTravelOrchestrator(config)
            except Exception:
                logger.exception(
                    "[VoiceLiveACSHandler] Failed to init specialist orchestrator; "
                    "disabling specialist tool calling"
                )
                self._enable_specialists = False

        # Running transcript used as context for the specialist orchestrator.
        self._conversation_history: list[dict[str, str]] = []

        # Specialist tool-call flow state (one in-flight tool call at a time).
        self._tool_call: Optional[dict] = None
        self._tool_result: Optional[dict] = None
        self._tool_result_ready = asyncio.Event()
        self._tool_ack_sent = False
        self._tool_result_presented = False

    def _generate_guid(self):
        return str(uuid.uuid4())

    async def connect(self):
        """Connects to Azure Voice Live API via the configured transport."""
        # Select a random persona for this session
        self.persona = get_random_persona(self._persona_context)
        logger.info(f"[VoiceLiveACSHandler] Selected persona: {self.persona['name']}")
        
        # Build URL using factory
        url = TransportFactory.build_url(self._transport_type, self.endpoint, self.model)

        headers = {"x-ms-client-request-id": self._generate_guid()}

        if self.client_id:
            # Try managed identity first (works on Azure), fall back to DefaultAzureCredential (local dev)
            token = None
            try:
                async with ManagedIdentityCredential(client_id=self.client_id) as credential:
                    token = await credential.get_token(
                        "https://cognitiveservices.azure.com/.default"
                    )
                    logger.info("[VoiceLiveACSHandler] Authenticated via managed identity")
            except Exception as mi_err:
                logger.warning(f"[VoiceLiveACSHandler] Managed identity unavailable: {mi_err}")
                if self.api_key:
                    logger.info("[VoiceLiveACSHandler] Falling back to API key auth")
                else:
                    logger.info("[VoiceLiveACSHandler] Falling back to DefaultAzureCredential")
                    try:
                        async with DefaultAzureCredential() as credential:
                            token = await credential.get_token(
                                "https://cognitiveservices.azure.com/.default"
                            )
                            logger.info("[VoiceLiveACSHandler] Authenticated via DefaultAzureCredential")
                    except Exception as dac_err:
                        logger.warning(f"[VoiceLiveACSHandler] DefaultAzureCredential failed: {dac_err}")

            if token:
                headers["Authorization"] = f"Bearer {token.token}"
            elif self.api_key:
                headers["api-key"] = self.api_key
            else:
                raise RuntimeError("No valid credentials available. Set AZURE_VOICE_LIVE_API_KEY or log in via Azure CLI.")
        else:
            headers["api-key"] = self.api_key

        # Create and connect transport
        self._transport = TransportFactory.create(self._transport_type)
        await self._transport.connect(url, headers)
        logger.info(f"[VoiceLiveACSHandler] Connected via {self._transport_type} transport")

        await self._transport.send_json(
            session_config(
                self.persona,
                self._prompt_file,
                self._persona_context,
                self._enable_specialists,
            )
        )
        await self._transport.send_json({"type": "response.create"})

        # Start transcription storage with persona name
        self._transcription_storage.start_conversation()

        asyncio.create_task(self._receiver_loop())
        self.send_task = asyncio.create_task(self._sender_loop())

    async def init_incoming_websocket(self, socket, is_raw_audio=True):
        """Sets up incoming ACS WebSocket."""
        self.incoming_websocket = socket
        self.is_raw_audio = is_raw_audio

    async def audio_to_voicelive(self, audio_b64: str):
        """Queues audio data to be sent to Voice Live API."""
        await self.send_queue.put(
            json.dumps({"type": "input_audio_buffer.append", "audio": audio_b64})
        )

    async def _send_json(self, obj):
        """Sends a JSON object via the transport."""
        if self._transport:
            await self._transport.send_json(obj)

    async def _sender_loop(self):
        """Continuously sends messages from the queue to Voice Live via transport."""
        try:
            while True:
                msg = await self.send_queue.get()
                if self._transport:
                    await self._transport.send(msg)
        except Exception:
            logger.exception("[VoiceLiveACSHandler] Sender loop error")

    async def _receiver_loop(self):
        """Handles incoming events from Voice Live via the transport."""
        try:
            async for message in self._transport.receive():
                event = json.loads(message)
                event_type = event.get("type")

                match event_type:
                    case "session.created":
                        session_id = event.get("session", {}).get("id")
                        logger.info("[VoiceLiveACSHandler] Session ID: %s", session_id)

                    case "input_audio_buffer.cleared":
                        logger.info("Input Audio Buffer Cleared Message")

                    case "input_audio_buffer.speech_started":
                        logger.info(
                            "Voice activity detection started at %s ms",
                            event.get("audio_start_ms"),
                        )
                        await self.stop_audio()

                    case "input_audio_buffer.speech_stopped":
                        logger.info("Speech stopped")

                    case "conversation.item.input_audio_transcription.completed":
                        transcript = event.get("transcript")
                        logger.info("User: %s", transcript)
                        # Send user transcription to frontend
                        if transcript:
                            await self.send_message(
                                json.dumps({"Kind": "UserTranscription", "Text": transcript})
                            )
                            # Save user message to transcription storage
                            self._transcription_storage.add_user_message(transcript)

                    case "conversation.item.input_audio_transcription.failed":
                        error_msg = event.get("error")
                        logger.warning("Transcription Error: %s", error_msg)

                    case "response.done":
                        response = event.get("response", {})
                        logger.info("Response Done: Id=%s", response.get("id"))
                        if response.get("status_details"):
                            logger.info(
                                "Status Details: %s",
                                json.dumps(response["status_details"], indent=2),
                            )

                    case "response.audio_transcript.done":
                        transcript = event.get("transcript")
                        logger.info("AI: %s", transcript)
                        await self.send_message(
                            json.dumps({"Kind": "Transcription", "Text": transcript})
                        )
                        # Save agent message to transcription storage with persona name
                        if transcript:
                            agent_name = self.persona["name"] if self.persona else None
                            self._transcription_storage.add_agent_message(transcript, agent_name)

                    case "response.audio.delta":
                        delta = event.get("delta")
                        audio_bytes = base64.b64decode(delta)
                        
                        # Check if ambient mixing is enabled
                        if self._ambient_mixer is not None and self._ambient_mixer.is_enabled():
                            # Buffer TTS for continuous output mixing
                            async with self._tts_buffer_lock:
                                self._tts_output_buffer.extend(audio_bytes)
                                # Warn if buffer is getting large, but NEVER drop audio
                                if len(self._tts_output_buffer) > self._max_buffer_size:
                                    if not self._buffer_warning_logged:
                                        logger.warning(
                                            f"TTS buffer large: {len(self._tts_output_buffer)} bytes. "
                                            "Speech may be delayed but will not be cut."
                                        )
                                        self._buffer_warning_logged = True
                                elif self._buffer_warning_logged and len(self._tts_output_buffer) < self._max_buffer_size // 2:
                                    self._buffer_warning_logged = False  # Reset warning flag
                        else:
                            # No ambient - send immediately (original behavior)
                            if self.is_raw_audio:
                                await self.send_message(audio_bytes)
                            else:
                                await self.voicelive_to_acs(delta)

                    case "error":
                        logger.error("Voice Live Error: %s", event)

                    case _:
                        logger.debug(
                            "[VoiceLiveACSHandler] Other event: %s", event_type
                        )
        except Exception:
            logger.exception("[VoiceLiveACSHandler] Receiver loop error")

    async def send_message(self, message: Data):
        """Sends data back to client WebSocket."""
        try:
            await self.incoming_websocket.send(message)
        except Exception:
            logger.exception("[VoiceLiveACSHandler] Failed to send message")

    async def voicelive_to_acs(self, base64_data):
        """Converts Voice Live audio delta to ACS audio message."""
        try:
            data = {
                "Kind": "AudioData",
                "AudioData": {"Data": base64_data},
                "StopAudio": None,
            }
            await self.send_message(json.dumps(data))
        except Exception:
            logger.exception("[VoiceLiveACSHandler] Error in voicelive_to_acs")

    async def stop_audio(self):
        """Sends a StopAudio signal to ACS."""
        stop_audio_data = {"Kind": "StopAudio", "AudioData": None, "StopAudio": {}}
        await self.send_message(json.dumps(stop_audio_data))
        
        # Clear TTS buffer when user starts speaking
        if self._ambient_mixer is not None:
            async with self._tts_buffer_lock:
                self._tts_output_buffer.clear()
                self._tts_playback_started = False

    async def _send_continuous_audio(self, chunk_size: int) -> None:
        """
        Send continuous audio (ambient + TTS if available) back to client.
        
        Called for every incoming audio frame, ensuring continuous output.
        Uses buffered TTS with minimum buffer threshold to prevent mid-word cuts.
        
        Args:
            chunk_size: Size of audio chunk to send (matches incoming frame size)
        """
        if self._ambient_mixer is None or not self._ambient_mixer.is_enabled():
            return  # Ambient disabled, skip
            
        try:
            async with self._tts_buffer_lock:
                buffer_len = len(self._tts_output_buffer)
                
                # Always get a consistent ambient chunk first
                ambient_bytes = self._ambient_mixer.get_ambient_only_chunk(chunk_size)
                
                # Determine if we should play TTS
                should_play_tts = False
                if self._tts_playback_started:
                    # Already playing - continue until buffer empty
                    if buffer_len >= chunk_size:
                        should_play_tts = True
                    elif buffer_len > 0:
                        # Partial buffer but still playing - use what we have
                        should_play_tts = True
                    else:
                        # Buffer empty - stop playback mode
                        self._tts_playback_started = False
                else:
                    # Not yet playing - wait for minimum buffer
                    if buffer_len >= self._min_buffer_to_start:
                        self._tts_playback_started = True
                        should_play_tts = True
                
                if should_play_tts and buffer_len >= chunk_size:
                    # Full TTS chunk available - add TTS on top of ambient
                    tts_chunk = bytes(self._tts_output_buffer[:chunk_size])
                    del self._tts_output_buffer[:chunk_size]
                    
                    # Mix: ambient (constant) + TTS
                    ambient = np.frombuffer(ambient_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                    tts = np.frombuffer(tts_chunk, dtype=np.int16).astype(np.float32) / 32768.0
                    mixed = ambient + tts
                    mixed = np.clip(mixed, -0.95, 0.95)  # Soft limit
                    output_bytes = (mixed * 32767).astype(np.int16).tobytes()
                    
                elif should_play_tts and buffer_len > 0:
                    # Partial TTS remaining at end of speech - drain it
                    tts_chunk = bytes(self._tts_output_buffer[:])
                    self._tts_output_buffer.clear()
                    self._tts_playback_started = False
                    
                    ambient = np.frombuffer(ambient_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                    
                    # Only mix TTS for the portion we have
                    tts_samples = len(tts_chunk) // 2
                    tts = np.frombuffer(tts_chunk, dtype=np.int16).astype(np.float32) / 32768.0
                    ambient[:tts_samples] += tts
                    mixed = np.clip(ambient, -0.95, 0.95)
                    output_bytes = (mixed * 32767).astype(np.int16).tobytes()
                    
                else:
                    # No TTS ready - just send constant ambient
                    output_bytes = ambient_bytes
            
            # Send to client
            if self.is_raw_audio:
                # Web browser - raw bytes
                await self.send_message(output_bytes)
            else:
                # Phone call - JSON wrapped
                output_b64 = base64.b64encode(output_bytes).decode("ascii")
                data = {
                    "Kind": "AudioData",
                    "AudioData": {"Data": output_b64},
                    "StopAudio": None,
                }
                await self.send_message(json.dumps(data))
                
        except Exception:
            logger.exception("[VoiceLiveACSHandler] Error in _send_continuous_audio")

    async def acs_to_voicelive(self, stream_data):
        """Processes audio from ACS and forwards to Voice Live if not silent."""
        try:
            data = json.loads(stream_data)
            if data.get("kind") == "AudioData":
                audio_data = data.get("audioData", {})
                incoming_data = audio_data.get("data", "")
                
                # Determine chunk size from incoming audio
                if incoming_data:
                    incoming_bytes = base64.b64decode(incoming_data)
                    chunk_size = len(incoming_bytes)
                else:
                    chunk_size = DEFAULT_CHUNK_SIZE
                
                # Send continuous audio back to caller (ambient + TTS mixed)
                await self._send_continuous_audio(chunk_size)
                
                # Forward non-silent audio to Voice Live (existing logic)
                if not audio_data.get("silent", True):
                    await self.audio_to_voicelive(audio_data.get("data"))
        except Exception:
            logger.exception("[VoiceLiveACSHandler] Error processing ACS audio")

    async def web_to_voicelive(self, audio_bytes):
        """Encodes raw audio bytes and sends to Voice Live API."""
        chunk_size = len(audio_bytes)
        
        # Send continuous audio back to browser (ambient + TTS mixed)
        await self._send_continuous_audio(chunk_size)
        
        # Forward to Voice Live
        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
        await self.audio_to_voicelive(audio_b64)

    async def stop_audio_output(self):
        """Clean up and save transcription when connection ends."""
        try:
            # Stop the sender task if running
            if self.send_task:
                self.send_task.cancel()
                self.send_task = None
            
            # Close the transport connection
            if self._transport:
                await self._transport.close()
                self._transport = None
            
            # Save the transcription
            self._transcription_storage.end_conversation()
            
            logger.info("[VoiceLiveACSHandler] Audio output stopped and transcription saved")
        except Exception:
            logger.exception("[VoiceLiveACSHandler] Error stopping audio output")
