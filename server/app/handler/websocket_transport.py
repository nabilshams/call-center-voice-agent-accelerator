"""WebSocket-based transport for Voice Live API (existing protocol)."""

import logging
from typing import AsyncIterator

from websockets.asyncio.client import connect as ws_connect

from .voice_live_transport import VoiceLiveTransport

logger = logging.getLogger(__name__)


class WebSocketTransport(VoiceLiveTransport):
    """Connects to Voice Live API using WebSocket protocol.

    This is the original transport that sends audio as data frames
    over a WebSocket connection to /voice-live/realtime.
    """

    API_PATH = "/voice-live/realtime"
    API_VERSION = "2025-05-01-preview"

    def __init__(self):
        self._ws = None

    @property
    def is_connected(self) -> bool:
        return self._ws is not None

    async def connect(self, url: str, headers: dict[str, str]) -> None:
        self._ws = await ws_connect(url, additional_headers=headers)
        logger.info("[WebSocketTransport] Connected to Voice Live API")

    async def send(self, message: str) -> None:
        if self._ws:
            await self._ws.send(message)

    async def receive(self) -> AsyncIterator[str]:
        async for message in self._ws:
            yield message

    async def close(self) -> None:
        if self._ws:
            await self._ws.close()
            self._ws = None
            logger.info("[WebSocketTransport] Connection closed")

    @classmethod
    def build_url(cls, endpoint: str, model: str) -> str:
        """Build the WebSocket URL for Voice Live API.

        Args:
            endpoint: The base Voice Live endpoint (https://...).
            model: The model name to use.

        Returns:
            The full wss:// URL for the WebSocket connection.
        """
        endpoint = endpoint.rstrip("/")
        model = model.strip()
        url = f"{endpoint}{cls.API_PATH}?api-version={cls.API_VERSION}&model={model}"
        return url.replace("https://", "wss://")
