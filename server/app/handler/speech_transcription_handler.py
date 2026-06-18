"""Handler for real-time speech transcription using Azure Speech SDK."""

import asyncio
import json
import logging
import queue
from datetime import datetime
from typing import Optional

import azure.cognitiveservices.speech as speechsdk
from azure.identity import ManagedIdentityCredential

from .transcription_storage import TranscriptionStorage
from .persona_inference import PersonaInferenceService
from .recap_inference import RecapInferenceService

logger = logging.getLogger(__name__)


class SpeechTranscriptionHandler:
    """Handles real-time speech-to-text transcription using Azure Speech SDK."""

    def __init__(self, config: dict, mode: str = "default"):
        """Initialize the transcription handler.
        
        Args:
            config: Application configuration containing Azure Speech credentials
            mode: 'default' for the generic live-transcription experience or
                'recap' for the clinician-focused experience (clinician/patient
                role taxonomy + structured SOAP note on stop).
        """
        self.mode = mode
        self.speech_key = config.get("AZURE_SPEECH_KEY", "")
        self.speech_region = config.get("AZURE_SPEECH_REGION", "")
        self.speech_endpoint = config.get("AZURE_SPEECH_ENDPOINT", "")
        self.speech_resource_id = config.get("AZURE_SPEECH_RESOURCE_ID", "")
        self.client_id = config.get("AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID", "")
        
        self.incoming_websocket = None
        self.conversation_transcriber: Optional[speechsdk.transcription.ConversationTranscriber] = None
        self.push_stream: Optional[speechsdk.audio.PushAudioInputStream] = None
        self._is_running = False
        self._sender_should_stop = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Thread-safe queue for passing messages from Speech SDK callbacks to async handler
        self._message_queue: queue.Queue = queue.Queue()

        # Transcription storage for saving conversations.
        # In recap mode, write to a date-partitioned 'recap/' prefix.
        storage_type = "recap" if mode == "recap" else "clinician_notes"
        self._transcription_storage = TranscriptionStorage(storage_type=storage_type)
        # Stable mapping from raw Speech SDK speaker IDs (e.g. "Guest-1") to friendly labels
        self._speaker_labels: dict[str, str] = {}

        # Inference service: clinician-focused for recap, generic otherwise.
        if mode == "recap":
            self._persona_inference = RecapInferenceService(
                config=config,
                message_sender=self._send_message_sync,
            )
        else:
            self._persona_inference = PersonaInferenceService(
                config=config,
                message_sender=self._send_message_sync,
            )

    async def init_websocket(self, websocket):
        """Initialize the incoming WebSocket connection.
        
        Args:
            websocket: The WebSocket connection from the client
        """
        self.incoming_websocket = websocket
        # Use get_running_loop() to get the correct event loop for async operations
        self._loop = asyncio.get_running_loop()
        self._persona_inference.set_loop(self._loop)
        logger.info(f"[SpeechTranscription] WebSocket initialized, loop: {self._loop}")

    async def start(self):
        """Start the speech recognition session."""
        try:
            # Create push stream for audio input (16kHz, 16-bit mono PCM)
            audio_format = speechsdk.audio.AudioStreamFormat(
                samples_per_second=16000,
                bits_per_sample=16,
                channels=1
            )
            self.push_stream = speechsdk.audio.PushAudioInputStream(audio_format)
            audio_config = speechsdk.audio.AudioConfig(stream=self.push_stream)

            # Create speech config
            speech_config = self._create_speech_config()

            # Enable detailed output for better transcription
            speech_config.output_format = speechsdk.OutputFormat.Detailed
            speech_config.set_profanity(speechsdk.ProfanityOption.Raw)

            # Create conversation transcriber for real-time diarization
            # ConversationTranscriber returns a `speaker_id` (e.g. "Guest-1", "Guest-2")
            # alongside each recognized result, enabling speaker-attributed transcripts.
            self.conversation_transcriber = speechsdk.transcription.ConversationTranscriber(
                speech_config=speech_config,
                audio_config=audio_config
            )

            # Connect event handlers (note: events are `transcribing`/`transcribed`)
            self.conversation_transcriber.transcribing.connect(self._on_recognizing)
            self.conversation_transcriber.transcribed.connect(self._on_recognized)
            self.conversation_transcriber.canceled.connect(self._on_canceled)
            self.conversation_transcriber.session_started.connect(self._on_session_started)
            self.conversation_transcriber.session_stopped.connect(self._on_session_stopped)

            # Start continuous transcription with diarization
            self.conversation_transcriber.start_transcribing_async()
            self._is_running = True
            self._sender_should_stop = False

            # Start the message sender task
            asyncio.create_task(self._message_sender())
            
            # Start transcription storage
            self._transcription_storage.start_conversation()
            
            logger.info("[SpeechTranscription] Started continuous recognition")

        except Exception as e:
            logger.exception("[SpeechTranscription] Failed to start recognition")
            await self._send_error(str(e))
            raise

    async def _message_sender(self):
        """Background task to send messages from queue to WebSocket.
        
        This runs in the async event loop and sends messages that were
        queued by the Speech SDK callbacks (which run in a different thread).
        """
        logger.info("[SpeechTranscription] Message sender task started")
        while not self._sender_should_stop:
            try:
                # Check queue for messages (non-blocking)
                try:
                    message = self._message_queue.get_nowait()
                    await self._send_message(message)
                except queue.Empty:
                    # No message, sleep briefly
                    await asyncio.sleep(0.01)
            except Exception as e:
                logger.error(f"[SpeechTranscription] Message sender error: {e}")
                await asyncio.sleep(0.1)
        # Drain any remaining queued messages (e.g., 'saved'/'summary'
        # produced during stop()) before exiting.
        while True:
            try:
                message = self._message_queue.get_nowait()
            except queue.Empty:
                break
            try:
                await self._send_message(message)
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f"[SpeechTranscription] Drain send error: {e}")
        logger.info("[SpeechTranscription] Message sender task stopped")

    def _create_speech_config(self) -> speechsdk.SpeechConfig:
        """Create Speech SDK configuration based on available credentials.
        
        Returns:
            Configured SpeechConfig object
        """
        logger.info(f"[SpeechTranscription] Config - region: '{self.speech_region}', client_id: '{self.client_id[:8] if self.client_id else 'None'}...', key: {'set' if self.speech_key else 'not set'}")
        
        if self.speech_key and self.speech_region:
            # Use API key (preferred for simplicity)
            logger.info("[SpeechTranscription] Using API key authentication")
            speech_config = speechsdk.SpeechConfig(
                subscription=self.speech_key,
                region=self.speech_region
            )
        elif self.client_id and self.speech_region:
            # Use managed identity with proper token format
            logger.info(f"[SpeechTranscription] Using managed identity authentication (client_id: {self.client_id})")
            credential = ManagedIdentityCredential(client_id=self.client_id)
            
            logger.info("[SpeechTranscription] Acquiring token from managed identity...")
            token = credential.get_token("https://cognitiveservices.azure.com/.default")
            logger.info("[SpeechTranscription] Token acquired successfully")
            
            # For managed identity with Speech SDK, use from_host with the regional endpoint
            # and set the authorization token with the AAD format
            host_url = f"wss://{self.speech_region}.stt.speech.microsoft.com"
            speech_config = speechsdk.SpeechConfig(host=host_url)
            
            # Set authorization token with proper AAD format for Cognitive Services
            if self.speech_resource_id:
                auth_token = f"aad#{self.speech_resource_id}#{token.token}"
                logger.info(f"[SpeechTranscription] Using AAD token format with resource ID")
            else:
                auth_token = token.token
                logger.info("[SpeechTranscription] Using plain AAD token (no resource ID)")
            speech_config.authorization_token = auth_token
        else:
            logger.error(f"[SpeechTranscription] Missing credentials - region: '{self.speech_region}', client_id present: {bool(self.client_id)}, key present: {bool(self.speech_key)}")
            raise ValueError(
                "Missing Azure Speech credentials. "
                "Set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION, or use managed identity."
            )
        
        # Set recognition language (can be made configurable)
        speech_config.speech_recognition_language = "en-US"
        
        return speech_config

    async def process_audio(self, audio_data: bytes):
        """Process incoming audio data from the client.
        
        Args:
            audio_data: Raw PCM audio bytes (16kHz, 16-bit mono)
        """
        if self.push_stream and self._is_running:
            try:
                self.push_stream.write(audio_data)
                # Log audio data receipt periodically
                if not hasattr(self, '_audio_count'):
                    self._audio_count = 0
                self._audio_count += 1
                if self._audio_count % 100 == 1:  # Log every 100th packet
                    logger.info(f"[SpeechTranscription] Received audio packet #{self._audio_count}, size: {len(audio_data)} bytes")
            except Exception as e:
                logger.error(f"[SpeechTranscription] Error writing audio: {e}")

    async def stop(self):
        """Stop the speech recognition session."""
        if not self._is_running and self.push_stream is None and self.conversation_transcriber is None:
            # Already stopped; nothing to do.
            return
        # Mark recognition as stopped so no new audio is accepted, but keep
        # the message sender alive until we've queued the final saved/summary
        # messages below.
        self._is_running = False

        if self.push_stream:
            try:
                self.push_stream.close()
            except Exception as e:
                logger.error(f"[SpeechTranscription] Error closing push stream: {e}")
            self.push_stream = None

        if self.conversation_transcriber:
            try:
                self.conversation_transcriber.stop_transcribing_async()
            except Exception as e:
                logger.error(f"[SpeechTranscription] Error stopping transcriber: {e}")
            self.conversation_transcriber = None

        # Save transcription when session ends; surface the blob URL to the client
        self._transcription_storage.end_conversation()
        blob_url = self._transcription_storage.last_saved_url
        if blob_url:
            self._send_message_sync({
                "type": "saved",
                "url": blob_url,
                "savedAt": datetime.now().isoformat(),
            })

        # Generate a final LLM-based summary while the client is still listening.
        # In recap mode this is a structured SOAP note; in default mode it is
        # the generic free-text summary + key_points + action_items.
        try:
            summary = await self._persona_inference.summarize()
            if summary:
                msg_type = "recap_note" if self.mode == "recap" else "summary"
                self._send_message_sync({"type": msg_type, **summary})
            else:
                logger.warning("[SpeechTranscription] Summary returned None")
                self._send_message_sync({
                    "type": "error",
                    "stage": "summary",
                    "message": (
                        "The clinical note could not be generated. The "
                        "transcript may have been too short, or the AI "
                        "service declined to respond. Please try again."
                    ),
                })
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f"[SpeechTranscription] Summary generation failed: {e}")
            self._send_message_sync({
                "type": "error",
                "stage": "summary",
                "message": f"Failed to generate clinical note: {e}",
            })

        # Now signal the message sender to stop; it will drain any queued
        # messages (saved + summary) before exiting.
        self._sender_should_stop = True
        # Give the sender a brief window to drain.
        await asyncio.sleep(0.5)

        try:
            await self._persona_inference.aclose()
        except Exception as e:
            logger.warning(f"[SpeechTranscription] Error closing persona inference: {e}")

        logger.info("[SpeechTranscription] Stopped recognition")

    def _resolve_speaker(self, raw_speaker_id: Optional[str]) -> str:
        """Map raw Speech SDK speaker IDs to friendly, stable display labels.

        Azure returns IDs like "Guest-1", "Guest-2", or "Unknown". We map each
        unique ID to "Speaker 1", "Speaker 2", etc., in first-seen order.
        """
        if not raw_speaker_id or raw_speaker_id.lower() == "unknown":
            return "Unknown"
        if raw_speaker_id not in self._speaker_labels:
            self._speaker_labels[raw_speaker_id] = f"Speaker {len(self._speaker_labels) + 1}"
        return self._speaker_labels[raw_speaker_id]

    def _on_recognizing(self, evt):
        """Handle partial transcription results (interim transcripts)."""
        logger.info(f"[SpeechTranscription] Transcribing event - Reason: {evt.result.reason}")
        if evt.result.reason == speechsdk.ResultReason.RecognizingSpeech:
            text = evt.result.text
            if text:
                speaker = self._resolve_speaker(getattr(evt.result, "speaker_id", None))
                logger.info(f"[SpeechTranscription] Partial [{speaker}]: {text}")
                self._send_message_sync({
                    "type": "partial",
                    "text": text,
                    "speaker": speaker,
                })

    def _on_recognized(self, evt):
        """Handle final transcription results."""
        logger.info(f"[SpeechTranscription] Transcribed event - Reason: {evt.result.reason}")
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            text = evt.result.text
            if text:
                speaker = self._resolve_speaker(getattr(evt.result, "speaker_id", None))
                logger.info(f"[SpeechTranscription] Final [{speaker}]: {text}")
                self._send_message_sync({
                    "type": "final",
                    "text": text,
                    "speaker": speaker,
                })
                # Persist with speaker label so the saved transcript is diarized
                self._transcription_storage.add_speaker_message(speaker, text)
                # Feed into persona inference (background, debounced)
                self._persona_inference.add_segment(speaker, text)
        elif evt.result.reason == speechsdk.ResultReason.NoMatch:
            no_match_detail = speechsdk.NoMatchDetails(evt.result)
            logger.info(f"[SpeechTranscription] No speech recognized - Reason: {no_match_detail.reason}")

    def _on_canceled(self, evt: speechsdk.SpeechRecognitionCanceledEventArgs):
        """Handle recognition cancellation."""
        # Always log the full cancellation event so silent failures are visible.
        reason = getattr(evt, "reason", None)
        error_code = getattr(evt, "error_code", None)
        error_details = getattr(evt, "error_details", None)
        logger.error(
            "[SpeechTranscription] Canceled event - reason: %s, error_code: %s, error_details: %s",
            reason, error_code, error_details,
        )
        if reason == speechsdk.CancellationReason.Error:
            self._send_message_sync({
                "type": "error",
                "message": f"Speech recognition error ({error_code}): {error_details}",
            })
        elif reason == speechsdk.CancellationReason.EndOfStream:
            logger.info("[SpeechTranscription] End of audio stream")

    def _on_session_started(self, evt: speechsdk.SessionEventArgs):
        """Handle session start event."""
        logger.info(f"[SpeechTranscription] Session started: {evt.session_id}")

    def _on_session_stopped(self, evt: speechsdk.SessionEventArgs):
        """Handle session stop event."""
        logger.info(f"[SpeechTranscription] Session stopped: {evt.session_id}")
        # Surface any error details that might be attached to the event.
        details = getattr(evt, "error_details", None)
        if details:
            logger.error(f"[SpeechTranscription] Session stopped with error_details: {details}")

    def _send_message_sync(self, message: dict):
        """Queue a message to be sent to the WebSocket client (thread-safe).
        
        This method is called from Speech SDK callbacks which run in a different thread.
        Instead of trying to send directly (which doesn't work with Quart's context-local
        websocket proxy), we put messages on a thread-safe queue that the async
        _message_sender task consumes.
        """
        try:
            self._message_queue.put_nowait(message)
            logger.debug(f"[SpeechTranscription] Queued message: {message.get('type')}")
        except Exception as e:
            logger.error(f"[SpeechTranscription] Failed to queue message: {e}")

    async def _send_message(self, message: dict):
        """Send a JSON message to the WebSocket client."""
        try:
            if self.incoming_websocket:
                msg_json = json.dumps(message)
                logger.info(f"[SpeechTranscription] Sending to client: {msg_json[:100]}...")
                await self.incoming_websocket.send(msg_json)
                logger.debug(f"[SpeechTranscription] Message sent successfully")
        except Exception as e:
            logger.error(f"[SpeechTranscription] Failed to send message: {e}")

    async def _send_error(self, error_message: str):
        """Send an error message to the WebSocket client."""
        await self._send_message({
            "type": "error",
            "message": error_message
        })
