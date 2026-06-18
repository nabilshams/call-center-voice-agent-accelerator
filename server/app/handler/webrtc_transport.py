"""WebRTC-based transport for Voice Live API (new protocol).

Uses a WebSocket control channel for SDP negotiation and session control,
with audio flowing over WebRTC RTP media tracks. Non-audio events are
exchanged over WebRTC data channels.

Reference: https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/
           voice-live-api-now-supports-webrtc-preview/4516002
"""

import asyncio
import json
import logging
from typing import AsyncIterator, Optional

from websockets.asyncio.client import connect as ws_connect

from .voice_live_transport import VoiceLiveTransport

logger = logging.getLogger(__name__)

try:
    from aiortc import (
        RTCPeerConnection,
        RTCSessionDescription,
        RTCConfiguration,
        RTCIceServer,
    )
    from aiortc.contrib.media import MediaBlackhole

    WEBRTC_AVAILABLE = True
except ImportError:
    WEBRTC_AVAILABLE = False
    logger.warning(
        "[WebRTCTransport] aiortc not installed. "
        "Install with: pip install aiortc"
    )


class WebRTCTransport(VoiceLiveTransport):
    """Connects to Voice Live API using WebRTC protocol.

    Architecture:
    - WebSocket control channel: SDP offer/answer negotiation, session config,
      control-plane messages, and error notifications.
    - WebRTC RTP tracks: Bi-directional audio streaming.
    - WebRTC data channels: Voice activity events and response lifecycle signals.

    Endpoint: /voice-live/realtime/calls
    API Version: 2026-01-01-preview
    """

    API_PATH = "/voice-live/realtime/calls"
    API_VERSION = "2026-01-01-preview"

    def __init__(self):
        if not WEBRTC_AVAILABLE:
            raise RuntimeError(
                "WebRTC transport requires 'aiortc' package. "
                "Install with: pip install aiortc"
            )
        self._ws = None  # Control channel WebSocket
        self._pc: Optional[RTCPeerConnection] = None
        self._data_channel = None
        self._message_queue: asyncio.Queue[str] = asyncio.Queue()
        self._audio_track = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self, url: str, headers: dict[str, str]) -> None:
        """Establish WebRTC connection via WebSocket signaling.

        Steps:
        1. Open WebSocket control channel
        2. Create RTCPeerConnection
        3. Create and send SDP offer
        4. Receive SDP answer from server
        5. Set remote description to complete negotiation
        """
        # Step 1: Open WebSocket control channel for signaling
        self._ws = await ws_connect(url, additional_headers=headers)
        logger.info("[WebRTCTransport] Control channel WebSocket connected")

        # Step 2: Create peer connection
        config = RTCConfiguration(
            iceServers=[RTCIceServer(urls=["stun:stun.l.google.com:19302"])]
        )
        self._pc = RTCPeerConnection(configuration=config)

        # Set up data channel for non-audio events
        self._data_channel = self._pc.createDataChannel("events")
        self._data_channel.on("message", self._on_data_channel_message)

        # Handle incoming audio tracks from server
        @self._pc.on("track")
        def on_track(track):
            logger.info(f"[WebRTCTransport] Received track: {track.kind}")
            if track.kind == "audio":
                self._audio_track = track
                asyncio.ensure_future(self._receive_audio_track(track))

        # Handle data channel opened by server
        @self._pc.on("datachannel")
        def on_datachannel(channel):
            logger.info(f"[WebRTCTransport] Server data channel: {channel.label}")
            channel.on("message", self._on_data_channel_message)

        # Step 3: Create SDP offer
        # Add audio transceiver for bi-directional audio
        self._pc.addTransceiver("audio", direction="sendrecv")

        offer = await self._pc.createOffer()
        await self._pc.setLocalDescription(offer)

        # Step 4: Send offer to server via control channel
        sdp_offer_msg = json.dumps({
            "type": "sdp.offer",
            "sdp": self._pc.localDescription.sdp
        })
        await self._ws.send(sdp_offer_msg)
        logger.info("[WebRTCTransport] SDP offer sent")

        # Step 5: Wait for SDP answer from server
        await self._wait_for_sdp_answer()
        self._connected = True
        logger.info("[WebRTCTransport] WebRTC connection established")

        # Start listening to control channel for session events
        asyncio.create_task(self._control_channel_listener())

    async def _wait_for_sdp_answer(self) -> None:
        """Wait for and process SDP answer from the server."""
        async for message in self._ws:
            event = json.loads(message)
            if event.get("type") == "sdp.answer":
                answer = RTCSessionDescription(sdp=event["sdp"], type="answer")
                await self._pc.setRemoteDescription(answer)
                logger.info("[WebRTCTransport] SDP answer received and set")
                return
            else:
                # Queue any other messages received during negotiation
                self._message_queue.put_nowait(json.dumps(event))

    async def _control_channel_listener(self) -> None:
        """Listen to WebSocket control channel for session/error events."""
        try:
            async for message in self._ws:
                event = json.loads(message)
                event_type = event.get("type", "")
                # Control-plane messages go to the message queue
                # for the receiver to process
                await self._message_queue.put(json.dumps(event))
        except Exception:
            logger.exception("[WebRTCTransport] Control channel listener error")

    def _on_data_channel_message(self, message: str) -> None:
        """Handle messages from WebRTC data channel (voice activity, lifecycle)."""
        try:
            self._message_queue.put_nowait(message)
        except Exception:
            logger.exception("[WebRTCTransport] Error handling data channel message")

    async def _receive_audio_track(self, track) -> None:
        """Receive audio frames from the server's RTP track."""
        try:
            while True:
                frame = await track.recv()
                # Convert audio frame to the format expected by the handler
                # and queue as a response.audio.delta event
                audio_data = bytes(frame.planes[0])
                import base64
                delta_b64 = base64.b64encode(audio_data).decode("ascii")
                event = json.dumps({
                    "type": "response.audio.delta",
                    "delta": delta_b64
                })
                await self._message_queue.put(event)
        except Exception:
            logger.debug("[WebRTCTransport] Audio track ended")

    async def send(self, message: str) -> None:
        """Send message via the appropriate channel.

        Audio buffer appends go through the data channel (when open),
        session/control messages go through the WebSocket control channel.
        """
        try:
            event = json.loads(message)
            event_type = event.get("type", "")

            if event_type == "input_audio_buffer.append":
                # Audio data goes through data channel for lower latency
                if self._data_channel and self._data_channel.readyState == "open":
                    self._data_channel.send(message)
                elif self._ws:
                    # Fallback to control channel
                    await self._ws.send(message)
            else:
                # Control/session messages go through WebSocket
                if self._ws:
                    await self._ws.send(message)
        except Exception:
            logger.exception("[WebRTCTransport] Error sending message")

    async def receive(self) -> AsyncIterator[str]:
        """Yield messages from both data channel and control channel."""
        while self._connected:
            try:
                message = await asyncio.wait_for(
                    self._message_queue.get(), timeout=1.0
                )
                yield message
            except asyncio.TimeoutError:
                continue

    async def close(self) -> None:
        """Close WebRTC peer connection and control channel."""
        self._connected = False

        if self._pc:
            await self._pc.close()
            self._pc = None
            logger.info("[WebRTCTransport] Peer connection closed")

        if self._ws:
            await self._ws.close()
            self._ws = None
            logger.info("[WebRTCTransport] Control channel closed")

    @classmethod
    def build_url(cls, endpoint: str, model: str) -> str:
        """Build the WebSocket control channel URL for WebRTC negotiation.

        Args:
            endpoint: The base Voice Live endpoint (https://...).
            model: The model name to use.

        Returns:
            The full wss:// URL for the control channel.
        """
        endpoint = endpoint.rstrip("/")
        model = model.strip()
        url = f"{endpoint}{cls.API_PATH}?api-version={cls.API_VERSION}&model={model}"
        return url.replace("https://", "wss://")
