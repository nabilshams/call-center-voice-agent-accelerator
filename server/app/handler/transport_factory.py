"""Factory for creating Voice Live API transport instances.

Uses the Strategy pattern to select the appropriate transport
based on configuration, defaulting to WebSocket for backward compatibility.
"""

import logging
from typing import Literal

from .voice_live_transport import VoiceLiveTransport
from .websocket_transport import WebSocketTransport
from .webrtc_transport import WebRTCTransport, WEBRTC_AVAILABLE

logger = logging.getLogger(__name__)

TransportType = Literal["websocket", "webrtc"]


class TransportFactory:
    """Creates the appropriate VoiceLiveTransport based on configuration."""

    @staticmethod
    def create(transport_type: TransportType = "websocket") -> VoiceLiveTransport:
        """Create a transport instance.

        Args:
            transport_type: The transport protocol to use.
                - "websocket": Original WebSocket transport (default).
                - "webrtc": New WebRTC transport with lower latency.

        Returns:
            A VoiceLiveTransport implementation.

        Raises:
            ValueError: If transport_type is not recognized.
            RuntimeError: If WebRTC is requested but aiortc is not installed.
        """
        match transport_type:
            case "websocket":
                logger.info("[TransportFactory] Creating WebSocket transport")
                return WebSocketTransport()
            case "webrtc":
                if not WEBRTC_AVAILABLE:
                    raise RuntimeError(
                        "WebRTC transport requires 'aiortc' package. "
                        "Install with: pip install aiortc"
                    )
                logger.info("[TransportFactory] Creating WebRTC transport")
                return WebRTCTransport()
            case _:
                raise ValueError(
                    f"Unknown transport type: '{transport_type}'. "
                    "Supported: 'websocket', 'webrtc'"
                )

    @staticmethod
    def build_url(
        transport_type: TransportType, endpoint: str, model: str
    ) -> str:
        """Build the connection URL for the given transport type.

        Args:
            transport_type: The transport protocol.
            endpoint: The base Voice Live endpoint.
            model: The model name.

        Returns:
            The full connection URL.
        """
        match transport_type:
            case "websocket":
                return WebSocketTransport.build_url(endpoint, model)
            case "webrtc":
                return WebRTCTransport.build_url(endpoint, model)
            case _:
                raise ValueError(f"Unknown transport type: '{transport_type}'")
