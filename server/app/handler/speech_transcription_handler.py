"""Handler for real-time speech transcription using Azure Speech SDK."""

import asyncio
import json
import logging
import queue
from typing import Optional

import azure.cognitiveservices.speech as speechsdk
from azure.identity import ManagedIdentityCredential

from .transcription_storage import TranscriptionStorage

logger = logging.getLogger(__name__)


class SpeechTranscriptionHandler:
    """Handles real-time speech-to-text transcription using Azure Speech SDK."""

    def __init__(self, config: dict):
        """Initialize the transcription handler.
        
        Args:
            config: Application configuration containing Azure Speech credentials
        """
        self.speech_key = config.get("AZURE_SPEECH_KEY", "")
        self.speech_region = config.get("AZURE_SPEECH_REGION", "")
        self.speech_endpoint = config.get("AZURE_SPEECH_ENDPOINT", "")
        self.speech_resource_id = config.get("AZURE_SPEECH_RESOURCE_ID", "")
        self.client_id = config.get("AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID", "")
        
        self.incoming_websocket = None
        self.speech_recognizer: Optional[speechsdk.SpeechRecognizer] = None
        self.push_stream: Optional[speechsdk.audio.PushAudioInputStream] = None
        self._is_running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Thread-safe queue for passing messages from Speech SDK callbacks to async handler
        self._message_queue: queue.Queue = queue.Queue()
        
        # Transcription storage for saving conversations
        self._transcription_storage = TranscriptionStorage(storage_type="clinician_notes")

    async def init_websocket(self, websocket):
        """Initialize the incoming WebSocket connection.
        
        Args:
            websocket: The WebSocket connection from the client
        """
        self.incoming_websocket = websocket
        # Use get_running_loop() to get the correct event loop for async operations
        self._loop = asyncio.get_running_loop()
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
            
            # Create recognizer
            self.speech_recognizer = speechsdk.SpeechRecognizer(
                speech_config=speech_config,
                audio_config=audio_config
            )

            # Connect event handlers
            self.speech_recognizer.recognizing.connect(self._on_recognizing)
            self.speech_recognizer.recognized.connect(self._on_recognized)
            self.speech_recognizer.canceled.connect(self._on_canceled)
            self.speech_recognizer.session_started.connect(self._on_session_started)
            self.speech_recognizer.session_stopped.connect(self._on_session_stopped)

            # Start continuous recognition
            self.speech_recognizer.start_continuous_recognition_async()
            self._is_running = True
            
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
        while self._is_running:
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
        self._is_running = False
        
        if self.push_stream:
            try:
                self.push_stream.close()
            except Exception as e:
                logger.error(f"[SpeechTranscription] Error closing push stream: {e}")
            self.push_stream = None

        if self.speech_recognizer:
            try:
                self.speech_recognizer.stop_continuous_recognition_async()
            except Exception as e:
                logger.error(f"[SpeechTranscription] Error stopping recognizer: {e}")
            self.speech_recognizer = None

        # Save transcription when session ends
        self._transcription_storage.end_conversation()

        logger.info("[SpeechTranscription] Stopped recognition")

    def _on_recognizing(self, evt: speechsdk.SpeechRecognitionEventArgs):
        """Handle partial recognition results (interim transcripts)."""
        logger.info(f"[SpeechTranscription] Recognizing event - Reason: {evt.result.reason}")
        if evt.result.reason == speechsdk.ResultReason.RecognizingSpeech:
            text = evt.result.text
            if text:
                logger.info(f"[SpeechTranscription] Partial: {text}")
                self._send_message_sync({
                    "type": "partial",
                    "text": text
                })

    def _on_recognized(self, evt: speechsdk.SpeechRecognitionEventArgs):
        """Handle final recognition results."""
        logger.info(f"[SpeechTranscription] Recognized event - Reason: {evt.result.reason}")
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            text = evt.result.text
            if text:
                logger.info(f"[SpeechTranscription] Final: {text}")
                self._send_message_sync({
                    "type": "final",
                    "text": text
                })
                # Save to transcription storage
                self._transcription_storage.add_user_message(text)
        elif evt.result.reason == speechsdk.ResultReason.NoMatch:
            no_match_detail = speechsdk.NoMatchDetails(evt.result)
            logger.info(f"[SpeechTranscription] No speech recognized - Reason: {no_match_detail.reason}")

    def _on_canceled(self, evt: speechsdk.SpeechRecognitionCanceledEventArgs):
        """Handle recognition cancellation."""
        if evt.reason == speechsdk.CancellationReason.Error:
            error_msg = f"Speech recognition error: {evt.error_details}"
            logger.error(f"[SpeechTranscription] {error_msg}")
            self._send_message_sync({
                "type": "error",
                "message": error_msg
            })
        elif evt.reason == speechsdk.CancellationReason.EndOfStream:
            logger.info("[SpeechTranscription] End of audio stream")

    def _on_session_started(self, evt: speechsdk.SessionEventArgs):
        """Handle session start event."""
        logger.info(f"[SpeechTranscription] Session started: {evt.session_id}")

    def _on_session_stopped(self, evt: speechsdk.SessionEventArgs):
        """Handle session stop event."""
        logger.info(f"[SpeechTranscription] Session stopped: {evt.session_id}")

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
