"""HTTP integration tests for the ``/travel/attachments`` routes.

Uses the Quart test client with the extractor + store replaced by simple
stubs so the tests run offline and are deterministic. What we exercise:

  * happy path: multipart upload -> 201 with public metadata
  * validation: missing session id / file, empty upload
  * extractor errors mapped to correct HTTP codes (415, 413, 422)
  * store cap error mapped to 409
  * list + delete + not-found semantics
"""

from __future__ import annotations

import io
import unittest
from unittest.mock import AsyncMock, patch

from werkzeug.datastructures import FileStorage

import server as server_module
from app.handler import attachment_extractor
from app.handler.attachment_extractor import (
    AttachmentExtractionError,
    AttachmentTooLargeError,
    ExtractedAttachment,
    UnsupportedAttachmentError,
)


def _upload(
    data: bytes = b"%PDF-1.4\nfake\n",
    filename: str = "boarding.pdf",
    content_type: str = "application/pdf",
) -> FileStorage:
    """Build a Quart-compatible file-upload object.

    Quart's test client expects ``FileStorage`` instances in ``files={}``,
    unlike Flask which accepts ``(BytesIO, filename, mime)`` tuples.
    """
    return FileStorage(
        stream=io.BytesIO(data),
        filename=filename,
        content_type=content_type,
    )


class TravelAttachmentRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = server_module.app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        # Isolate every test from any state prior runs might have left.
        server_module.attachment_store.clear_session("test-sess")

    async def asyncTearDown(self):
        server_module.attachment_store.clear_session("test-sess")

    # ------------------------------------------------------------------
    # POST /travel/attachments
    # ------------------------------------------------------------------

    async def test_upload_happy_path_returns_201_with_public_metadata(self):
        async def _fake_extract(*, raw_bytes, mime, filename):
            return ExtractedAttachment(
                kind="pdf", mime="application/pdf",
                extracted_text="Passenger: Ada Lovelace",
            )

        with patch.object(attachment_extractor, "extract", side_effect=_fake_extract):
            resp = await self.client.post(
                "/travel/attachments",
                form={"session_id": "test-sess"},
                files={"file": _upload()},
            )

        self.assertEqual(resp.status_code, 201)
        body = await resp.get_json()
        self.assertEqual(body["session_id"], "test-sess")
        self.assertEqual(body["filename"], "boarding.pdf")
        self.assertEqual(body["kind"], "pdf")
        self.assertEqual(body["mime"], "application/pdf")
        self.assertTrue(body["has_text"])
        self.assertIn("attachment_id", body)
        # Extracted text is a secret, must not leak to the browser.
        self.assertNotIn("Ada Lovelace", str(body))

    async def test_upload_missing_session_id_returns_400(self):
        resp = await self.client.post(
            "/travel/attachments",
            form={},
            files={"file": _upload(b"x")},
        )
        self.assertEqual(resp.status_code, 400)

    async def test_upload_missing_file_returns_400(self):
        resp = await self.client.post(
            "/travel/attachments",
            form={"session_id": "test-sess"},
        )
        self.assertEqual(resp.status_code, 400)

    async def test_upload_empty_file_returns_400(self):
        resp = await self.client.post(
            "/travel/attachments",
            form={"session_id": "test-sess"},
            files={"file": _upload(b"", filename="empty.pdf")},
        )
        self.assertEqual(resp.status_code, 400)

    async def test_upload_unsupported_mime_returns_415(self):
        with patch.object(
            attachment_extractor, "extract",
            side_effect=UnsupportedAttachmentError("nope"),
        ):
            resp = await self.client.post(
                "/travel/attachments",
                form={"session_id": "test-sess"},
                files={"file": _upload(b"body", filename="x.exe", content_type="application/x-msdownload")},
            )
        self.assertEqual(resp.status_code, 415)

    async def test_upload_too_large_returns_413(self):
        with patch.object(
            attachment_extractor, "extract",
            side_effect=AttachmentTooLargeError("too big"),
        ):
            resp = await self.client.post(
                "/travel/attachments",
                form={"session_id": "test-sess"},
                files={"file": _upload(b"body", filename="big.pdf")},
            )
        self.assertEqual(resp.status_code, 413)

    async def test_upload_extraction_failure_returns_422(self):
        with patch.object(
            attachment_extractor, "extract",
            side_effect=AttachmentExtractionError("could not parse"),
        ):
            resp = await self.client.post(
                "/travel/attachments",
                form={"session_id": "test-sess"},
                files={"file": _upload(b"body", filename="bad.pdf")},
            )
        self.assertEqual(resp.status_code, 422)

    async def test_upload_over_session_cap_returns_409(self):
        async def _fake_extract(*, raw_bytes, mime, filename):
            return ExtractedAttachment(kind="pdf", mime="application/pdf", extracted_text="x")

        with patch.object(server_module, "attachment_store") as store_mock:
            store_mock.add.side_effect = ValueError("Attachment limit reached (5 per session).")
            with patch.object(attachment_extractor, "extract", side_effect=_fake_extract):
                resp = await self.client.post(
                    "/travel/attachments",
                    form={"session_id": "test-sess"},
                    files={"file": _upload(b"body", filename="x.pdf")},
                )
        self.assertEqual(resp.status_code, 409)

    # ------------------------------------------------------------------
    # GET /travel/attachments
    # ------------------------------------------------------------------

    async def test_list_returns_public_metadata_only(self):
        server_module.attachment_store.add(
            session_id="test-sess",
            filename="boarding.pdf",
            kind="pdf",
            mime="application/pdf",
            size_bytes=42,
            extracted_text="SECRET-DO-NOT-LEAK",
        )
        resp = await self.client.get("/travel/attachments?session_id=test-sess")

        self.assertEqual(resp.status_code, 200)
        body = await resp.get_json()
        self.assertEqual(body["session_id"], "test-sess")
        self.assertEqual(len(body["attachments"]), 1)
        entry = body["attachments"][0]
        self.assertEqual(entry["filename"], "boarding.pdf")
        self.assertNotIn("extracted_text", entry)
        self.assertNotIn("SECRET-DO-NOT-LEAK", str(body))

    async def test_list_missing_session_id_returns_400(self):
        resp = await self.client.get("/travel/attachments")
        self.assertEqual(resp.status_code, 400)

    async def test_list_unknown_session_returns_empty_list(self):
        resp = await self.client.get("/travel/attachments?session_id=nobody-home")
        self.assertEqual(resp.status_code, 200)
        body = await resp.get_json()
        self.assertEqual(body["attachments"], [])

    # ------------------------------------------------------------------
    # DELETE /travel/attachments/<id>
    # ------------------------------------------------------------------

    async def test_delete_removes_record(self):
        record = server_module.attachment_store.add(
            session_id="test-sess",
            filename="boarding.pdf",
            kind="pdf",
            mime="application/pdf",
            size_bytes=42,
            extracted_text="anything",
        )
        resp = await self.client.delete(
            f"/travel/attachments/{record.attachment_id}?session_id=test-sess"
        )

        self.assertEqual(resp.status_code, 200)
        body = await resp.get_json()
        self.assertEqual(body["deleted"], record.attachment_id)
        self.assertEqual(server_module.attachment_store.list_session("test-sess"), [])

    async def test_delete_unknown_id_returns_404(self):
        resp = await self.client.delete(
            "/travel/attachments/does-not-exist?session_id=test-sess"
        )
        self.assertEqual(resp.status_code, 404)

    async def test_delete_missing_session_id_returns_400(self):
        resp = await self.client.delete("/travel/attachments/anything")
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
