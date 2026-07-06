"""Unit tests for ``app.handler.attachment_extractor``.

Covers the three MIME branches (JSON native, PDF + image via Document
Intelligence stub), the size/type gates, and truncation. Document
Intelligence itself is not exercised over the wire -- we assert the
graceful "endpoint not configured" fallback path (which is what runs in
CI and in local dev), and rely on a monkey-patched client for the
"happy path" and error-path tests.
"""

from __future__ import annotations

import io
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

from app.handler import attachment_extractor
from app.handler.attachment_extractor import (
    AttachmentExtractionError,
    AttachmentTooLargeError,
    ExtractedAttachment,
    MAX_EXTRACTED_CHARS,
    MAX_FILE_BYTES,
    MAX_JSON_CHARS,
    UnsupportedAttachmentError,
    extract,
)


def _tiny_pdf_bytes() -> bytes:
    """Return the smallest valid PDF header we can get away with.

    Doc Intelligence is stubbed in these tests, so the bytes never need to
    parse as a real document -- they only need to be non-empty and pass the
    size gate.
    """
    return b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n[minimal stub]\n%%EOF\n"


class ExtractJsonTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_json_returns_pretty_printed_kind_json(self):
        payload = {"passenger": "Ada", "seat": "12A", "meal": "veg"}
        raw = json.dumps(payload).encode("utf-8")

        result = await extract(raw_bytes=raw, mime="application/json", filename="pnr.json")

        self.assertIsInstance(result, ExtractedAttachment)
        self.assertEqual(result.kind, "json")
        self.assertEqual(result.mime, "application/json")
        # Pretty-printed => indented and contains the values.
        self.assertIn('"passenger": "Ada"', result.extracted_text)
        self.assertIn("\n", result.extracted_text)

    async def test_invalid_json_raises_extraction_error(self):
        raw = b"{not really json"

        with self.assertRaises(AttachmentExtractionError):
            await extract(raw_bytes=raw, mime="application/json", filename="broken.json")

    async def test_json_truncated_when_over_char_cap(self):
        # 5 000 entries of `"kNNNN": "vNNNN"` easily exceeds MAX_JSON_CHARS.
        big = {f"k{i:05d}": f"v{i:05d}" for i in range(5_000)}
        raw = json.dumps(big).encode("utf-8")

        result = await extract(raw_bytes=raw, mime="application/json", filename="big.json")

        self.assertTrue(result.extracted_text.endswith(
            "\n[...truncated for prompt size...]"
        ))
        # Body length equals cap + suffix (34 chars).
        body_len = len(result.extracted_text) - len("\n[...truncated for prompt size...]")
        self.assertEqual(body_len, MAX_JSON_CHARS)

    async def test_mime_case_and_charset_are_normalised(self):
        raw = json.dumps({"ok": True}).encode("utf-8")

        result = await extract(
            raw_bytes=raw,
            mime="Application/JSON; charset=utf-8",
            filename="mixed.json",
        )

        self.assertEqual(result.kind, "json")


class ExtractGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_file_rejected(self):
        with self.assertRaises(AttachmentExtractionError):
            await extract(raw_bytes=b"", mime="application/pdf", filename="empty.pdf")

    async def test_over_size_rejected(self):
        # One byte over the cap. Use a MIME that would otherwise be accepted
        # so we know the size check ran before the MIME check would.
        oversized = b"\0" * (MAX_FILE_BYTES + 1)

        with self.assertRaises(AttachmentTooLargeError):
            await extract(raw_bytes=oversized, mime="application/pdf", filename="huge.pdf")

    async def test_unsupported_mime_rejected(self):
        with self.assertRaises(UnsupportedAttachmentError):
            await extract(
                raw_bytes=b"hello world",
                mime="text/plain",
                filename="notes.txt",
            )

    async def test_unknown_mime_rejected(self):
        with self.assertRaises(UnsupportedAttachmentError):
            await extract(
                raw_bytes=b"\x00\x01\x02",
                mime="application/octet-stream",
                filename="mystery.bin",
            )


class ExtractPdfImageDevFallbackTests(unittest.IsolatedAsyncioTestCase):
    """When ``AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT`` is unset the extractor
    returns a helpful stub instead of raising -- this is the dev-loop path."""

    def setUp(self):
        # Wipe the env var and the module-level client cache so this test is
        # isolated from whatever the developer has running locally.
        self._prev = os.environ.pop("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", None)
        attachment_extractor._di_client = None  # type: ignore[attr-defined]

    def tearDown(self):
        if self._prev is not None:
            os.environ["AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"] = self._prev

    async def test_pdf_returns_stub_when_endpoint_unset(self):
        result = await extract(
            raw_bytes=_tiny_pdf_bytes(),
            mime="application/pdf",
            filename="boarding-pass.pdf",
        )

        self.assertEqual(result.kind, "pdf")
        self.assertEqual(result.mime, "application/pdf")
        self.assertTrue(result.extracted_text.startswith("[PDF 'boarding-pass.pdf'"))
        self.assertIn("Document Intelligence is not configured", result.extracted_text)

    async def test_image_returns_stub_when_endpoint_unset(self):
        result = await extract(
            raw_bytes=b"\x89PNG\r\n\x1a\nfake image bytes",
            mime="image/png",
            filename="passport.png",
        )

        self.assertEqual(result.kind, "image")
        self.assertTrue(result.extracted_text.startswith("[IMAGE 'passport.png'"))


class ExtractPdfWithMockedDocIntelligenceTests(unittest.IsolatedAsyncioTestCase):
    """When the endpoint IS set we route through Document Intelligence.

    The SDK is imported lazily inside the extractor, so we replace the
    module-level ``_di_client`` singleton with a mock and let the code
    exercise the analyse path against it.
    """

    def setUp(self):
        os.environ["AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"] = (
            "https://fake-di.cognitiveservices.azure.com/"
        )
        # Force _get_di_client to skip lazy construction by seeding the cache.
        self._client_mock = SimpleNamespace(close=AsyncMock())
        attachment_extractor._di_client = self._client_mock  # type: ignore[attr-defined]

    def tearDown(self):
        attachment_extractor._di_client = None  # type: ignore[attr-defined]
        os.environ.pop("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", None)

    async def _run_with_content(self, content: str) -> ExtractedAttachment:
        poller = SimpleNamespace(result=AsyncMock(return_value=SimpleNamespace(content=content)))
        self._client_mock.begin_analyze_document = AsyncMock(return_value=poller)
        return await extract(
            raw_bytes=_tiny_pdf_bytes(),
            mime="application/pdf",
            filename="hotel-conf.pdf",
        )

    async def test_pdf_content_returned_verbatim(self):
        result = await self._run_with_content("Hotel Wanderlux, Barcelona, check-in 2026-08-14")

        self.assertEqual(result.kind, "pdf")
        self.assertIn("Barcelona", result.extracted_text)

    async def test_empty_content_returns_helpful_stub(self):
        result = await self._run_with_content("")

        self.assertIn("produced no readable text", result.extracted_text)

    async def test_content_over_cap_is_truncated(self):
        oversized = "x" * (MAX_EXTRACTED_CHARS + 500)

        result = await self._run_with_content(oversized)

        self.assertTrue(result.extracted_text.endswith(
            "\n[...truncated for prompt size...]"
        ))
        body = result.extracted_text[: -len("\n[...truncated for prompt size...]")]
        self.assertEqual(len(body), MAX_EXTRACTED_CHARS)

    async def test_analyse_failure_raises_extraction_error(self):
        self._client_mock.begin_analyze_document = AsyncMock(
            side_effect=RuntimeError("service unavailable"),
        )

        with self.assertRaises(AttachmentExtractionError) as cm:
            await extract(
                raw_bytes=_tiny_pdf_bytes(),
                mime="application/pdf",
                filename="bad.pdf",
            )
        self.assertIn("service unavailable", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
