"""Abstract base class for Voice Live API transport strategies."""

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Optional

logger = logging.getLogger(__name__)


class VoiceLiveTransport(ABC):
    """Abstract interface for communicating with Azure Voice Live API.

    Implementations handle the connection protocol (WebSocket or WebRTC)
    while exposing a unified interface for sending/receiving messages.
    """

    @abstractmethod
    async def connect(self, url: str, headers: dict[str, str]) -> None:
        """Establish connection to Voice Live API.

        Args:
            url: The Voice Live API endpoint URL.
            headers: Authentication and request headers.
        """
        ...

    @abstractmethod
    async def send(self, message: str) -> None:
        """Send a text message (JSON) to Voice Live API.

        Args:
            message: JSON-encoded string to send.
        """
        ...

    @abstractmethod
    async def receive(self) -> AsyncIterator[str]:
        """Async iterator yielding messages from Voice Live API.

        Yields:
            JSON-encoded string messages from the API.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close the connection to Voice Live API."""
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the transport is currently connected."""
        ...

    async def send_json(self, obj: dict[str, Any]) -> None:
        """Convenience method to send a dict as JSON.

        Args:
            obj: Dictionary to serialize and send.
        """
        await self.send(json.dumps(obj))
