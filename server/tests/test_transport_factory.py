"""Tier-3 tests for ``TransportFactory``.

The factory routes voice-live traffic to one of two transports (WebSocket
by default, WebRTC when explicitly requested and ``aiortc`` is installed).
Getting the dispatch wrong would either silently fall back to the wrong
protocol at runtime or crash the server with an unhelpful stack.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.handler import transport_factory as tf
from app.handler.transport_factory import TransportFactory
from app.handler.websocket_transport import WebSocketTransport


# =========================================================================
# create() -- strategy dispatch
# =========================================================================


class CreateTests(unittest.TestCase):
    def test_default_is_websocket(self):
        transport = TransportFactory.create()
        self.assertIsInstance(transport, WebSocketTransport)

    def test_explicit_websocket_returns_websocket(self):
        transport = TransportFactory.create("websocket")
        self.assertIsInstance(transport, WebSocketTransport)

    def test_unknown_transport_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            TransportFactory.create("carrier-pigeon")
        # The message should tell operators exactly which options exist.
        self.assertIn("carrier-pigeon", str(ctx.exception))
        self.assertIn("websocket", str(ctx.exception))
        self.assertIn("webrtc", str(ctx.exception))

    def test_webrtc_without_aiortc_raises_runtime_error(self):
        # Force the "not installed" branch regardless of the real env.
        with patch.object(tf, "WEBRTC_AVAILABLE", False):
            with self.assertRaises(RuntimeError) as ctx:
                TransportFactory.create("webrtc")
        self.assertIn("aiortc", str(ctx.exception))

    def test_webrtc_with_aiortc_constructs_webrtc_transport(self):
        # Force the "installed" branch and mock the constructor so the test
        # does not need aiortc at runtime.
        sentinel = object()
        with patch.object(tf, "WEBRTC_AVAILABLE", True), \
             patch.object(tf, "WebRTCTransport", return_value=sentinel) as ctor:
            transport = TransportFactory.create("webrtc")
        ctor.assert_called_once_with()
        self.assertIs(transport, sentinel)


# =========================================================================
# build_url() -- URL construction dispatch
# =========================================================================


class BuildUrlTests(unittest.TestCase):
    ENDPOINT = "https://voicelive.example.com/"
    MODEL = "gpt-4o-realtime"

    def test_websocket_url_wraps_endpoint_and_model(self):
        url = TransportFactory.build_url("websocket", self.ENDPOINT, self.MODEL)
        # Trailing slash on endpoint must be stripped.
        self.assertNotIn("com//", url)
        # Model + api-version query params must be present.
        self.assertIn(f"model={self.MODEL}", url)
        self.assertIn("api-version=", url)
        # Scheme must be flipped to wss://.
        self.assertTrue(url.startswith("wss://"))
        self.assertNotIn("https://", url)

    def test_webrtc_url_shape(self):
        url = TransportFactory.build_url("webrtc", self.ENDPOINT, self.MODEL)
        # WebRTC builds a WS control channel URL with the same conventions.
        self.assertTrue(url.startswith("wss://"))
        self.assertIn(f"model={self.MODEL}", url)
        self.assertIn("api-version=", url)

    def test_build_url_strips_whitespace_from_model(self):
        url = TransportFactory.build_url("websocket", self.ENDPOINT, "  gpt-x  ")
        self.assertIn("model=gpt-x", url)

    def test_build_url_unknown_transport_raises(self):
        with self.assertRaises(ValueError):
            TransportFactory.build_url("smoke-signal", self.ENDPOINT, self.MODEL)


if __name__ == "__main__":
    unittest.main()
