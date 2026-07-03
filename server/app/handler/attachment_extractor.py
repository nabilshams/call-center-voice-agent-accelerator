"""Server-side text extraction for uploaded attachments.

Turns raw file bytes into a text string that can be safely prepended to a
TripPlannerAgent prompt. The current agent-framework release exposes only
``FoundryAgent.run(prompt: str)`` — no multipart / ChatMessage surface — so
"attachments" here are pre-extracted text blocks, not native image/file
inputs. See ``docs/travel-agency.md`` for the Phase 2 upgrade path (direct
``responses.create`` with ``input_image`` / ``input_file``).

PDFs and images are extracted through **Azure Document Intelligence**
(``prebuilt-read``) — a single OCR-capable service that handles text-native
PDFs, scanned PDFs, and photos of documents (passports, boarding passes,
booking confirmations) uniformly. This piggybacks on the existing
``Microsoft.CognitiveServices/accounts`` (``kind: AIServices``) resource; no
extra Azure resource is provisioned.

Supported today:
  - ``application/pdf``          -> Document Intelligence ``prebuilt-read``.
  - ``image/*`` (jpg, png, webp, tiff, bmp, heif) -> ``prebuilt-read``.
  - ``application/json``          -> parsed + pretty-printed JSON, truncated.

Rejects everything else with ``UnsupportedAttachmentError`` so callers can
return HTTP 415 without leaking implementation details.

Environment:
  - ``AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT`` (required for PDF/image
    extraction; e.g. ``https://<account>.cognitiveservices.azure.com/``).
    When unset (local dev without provisioning), PDF/image uploads return
    an informative stub instead of raising, so the rest of the pipeline
    keeps working.
  - Uses ``DefaultAzureCredential`` — the container app's managed identity
    needs the ``Cognitive Services User`` role on the AI Services account
    (granted in ``infra/modules/roleassignments.bicep``).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from azure.ai.documentintelligence.aio import DocumentIntelligenceClient

logger = logging.getLogger(__name__)


ALLOWED_MIMES = {
    "application/pdf",
    "application/json",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/tiff",
    "image/bmp",
    "image/heif",
}

# Per-file size caps enforced at extract-time. The Quart-level
# ``MAX_CONTENT_LENGTH`` gives a hard cutoff; this is a softer secondary check
# so we return a clear error before doing expensive parsing.
MAX_FILE_BYTES = 10 * 1024 * 1024        # 10 MB per file
MAX_EXTRACTED_CHARS = 20_000             # cap what we inject into the prompt
MAX_JSON_CHARS = 8_000

_DOC_INTELLIGENCE_ENDPOINT_ENV = "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"

# Lazily-created singleton client. The Document Intelligence SDK is safe to
# reuse across requests; opening a fresh client per upload would cost a TCP +
# TLS handshake every time.
_di_client: "DocumentIntelligenceClient | None" = None


class UnsupportedAttachmentError(ValueError):
    """Raised for a MIME/extension we deliberately do not accept."""


class AttachmentTooLargeError(ValueError):
    """Raised when a file exceeds ``MAX_FILE_BYTES``."""


class AttachmentExtractionError(RuntimeError):
    """Raised when extraction fails despite the MIME/size being acceptable."""


@dataclass
class ExtractedAttachment:
    kind: str            # "pdf" | "image" | "json"
    mime: str
    extracted_text: str  # may be a stub if Doc Intelligence is not configured


async def extract(
    *,
    raw_bytes: bytes,
    mime: str,
    filename: str,
) -> ExtractedAttachment:
    """Normalize + extract. Returns text plus metadata."""
    if not raw_bytes:
        raise AttachmentExtractionError("Empty file.")
    if len(raw_bytes) > MAX_FILE_BYTES:
        raise AttachmentTooLargeError(
            f"File exceeds per-file limit of {MAX_FILE_BYTES // (1024 * 1024)} MB."
        )
    mime = (mime or "").lower().split(";")[0].strip()
    if mime not in ALLOWED_MIMES:
        raise UnsupportedAttachmentError(
            f"Unsupported MIME type '{mime}'. Allowed: {', '.join(sorted(ALLOWED_MIMES))}."
        )

    if mime == "application/json":
        return _extract_json(raw_bytes, filename)

    # Everything else (PDF + images) routes through Document Intelligence.
    kind = "pdf" if mime == "application/pdf" else "image"
    return await _extract_with_document_intelligence(
        raw_bytes=raw_bytes,
        mime=mime,
        filename=filename,
        kind=kind,
    )


async def aclose() -> None:
    """Close the cached Document Intelligence client (call on shutdown)."""
    global _di_client
    if _di_client is not None:
        await _di_client.close()
        _di_client = None


# ---------------------------------------------------------------------------
# Per-kind extractors
# ---------------------------------------------------------------------------


async def _extract_with_document_intelligence(
    *,
    raw_bytes: bytes,
    mime: str,
    filename: str,
    kind: str,
) -> ExtractedAttachment:
    endpoint = os.environ.get(_DOC_INTELLIGENCE_ENDPOINT_ENV, "").strip()
    if not endpoint:
        # Graceful dev-mode fallback: keep the upload flow working locally
        # without forcing every contributor to provision Doc Intelligence.
        stub = (
            f"[{kind.upper()} '{filename}' uploaded but Document Intelligence is "
            f"not configured (set ${_DOC_INTELLIGENCE_ENDPOINT_ENV}). Ask the "
            "customer for the key details you need (e.g. arrival time from a "
            "boarding pass, hotel address from a confirmation) to anchor the "
            "itinerary.]"
        )
        logger.warning(
            "doc_intelligence_endpoint_missing filename=%s kind=%s",
            filename,
            kind,
        )
        return ExtractedAttachment(kind=kind, mime=mime, extracted_text=stub)

    client = await _get_di_client(endpoint)

    try:
        # Import here so a missing dependency surfaces as an extraction error
        # (returned to the client) rather than a server import-time crash.
        from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
    except ImportError as exc:  # pragma: no cover - dependency listed in pyproject
        raise AttachmentExtractionError(
            "azure-ai-documentintelligence is not installed; "
            "add it to server/pyproject.toml."
        ) from exc

    try:
        poller = await client.begin_analyze_document(
            "prebuilt-read",
            AnalyzeDocumentRequest(bytes_source=raw_bytes),
        )
        result = await poller.result()
    except Exception as exc:
        logger.exception(
            "doc_intelligence_analyze_failed filename=%s kind=%s",
            filename,
            kind,
        )
        raise AttachmentExtractionError(
            f"Document Intelligence could not read '{filename}': {exc}"
        ) from exc

    content = (getattr(result, "content", "") or "").strip()
    if not content:
        stub = (
            f"[{kind.upper()} '{filename}' produced no readable text via "
            "Document Intelligence. Ask the customer to resend a clearer copy "
            "or describe the key details.]"
        )
        return ExtractedAttachment(kind=kind, mime=mime, extracted_text=stub)

    if len(content) > MAX_EXTRACTED_CHARS:
        content = content[:MAX_EXTRACTED_CHARS] + "\n[...truncated for prompt size...]"
    return ExtractedAttachment(kind=kind, mime=mime, extracted_text=content)


async def _get_di_client(endpoint: str) -> "DocumentIntelligenceClient":
    global _di_client
    if _di_client is not None:
        return _di_client

    try:
        from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
        from azure.identity.aio import DefaultAzureCredential
    except ImportError as exc:  # pragma: no cover - dependency listed in pyproject
        raise AttachmentExtractionError(
            "azure-ai-documentintelligence is not installed; "
            "add it to server/pyproject.toml."
        ) from exc

    _di_client = DocumentIntelligenceClient(
        endpoint=endpoint,
        credential=DefaultAzureCredential(),
    )
    return _di_client


def _extract_json(raw_bytes: bytes, filename: str) -> ExtractedAttachment:
    try:
        parsed = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AttachmentExtractionError(
            f"Could not parse JSON '{filename}': {exc}"
        ) from exc

    pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
    if len(pretty) > MAX_JSON_CHARS:
        pretty = pretty[:MAX_JSON_CHARS] + "\n[...truncated for prompt size...]"
    return ExtractedAttachment(kind="json", mime="application/json", extracted_text=pretty)
