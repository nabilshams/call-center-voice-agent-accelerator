"""In-memory session-scoped attachment store for TripPlannerAgent.

Holds extracted text (plus small metadata) for files uploaded via
``POST /travel/attachments``. Records are keyed by ``(session_id,
attachment_id)`` and evicted after ``ttl_seconds`` of inactivity so an
abandoned browser tab does not leak memory.

The store deliberately keeps **only** the extracted text plus size / mime /
filename metadata — the raw bytes are dropped after extraction. This keeps
memory footprint predictable (a 10 MB PDF collapses to a few KB of text) and
avoids storing potentially sensitive raw uploads longer than needed.

Not suitable for cross-process or cross-restart durability: use a Blob-backed
store (see ``TranscriptionStorage`` for the pattern) if you need that.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Iterable

logger = logging.getLogger(__name__)


@dataclass
class AttachmentRecord:
    attachment_id: str
    session_id: str
    filename: str
    kind: str            # "pdf" | "image" | "json"
    mime: str
    size_bytes: int
    extracted_text: str  # may be a stub like "[image content not extracted...]"
    uploaded_at: float = field(default_factory=time.time)

    def to_public_dict(self) -> dict:
        """Metadata safe to return to the browser (no full text)."""
        return {
            "attachment_id": self.attachment_id,
            "filename": self.filename,
            "kind": self.kind,
            "mime": self.mime,
            "size_bytes": self.size_bytes,
            "has_text": bool(self.extracted_text) and not self.extracted_text.startswith("["),
            "uploaded_at": self.uploaded_at,
        }


class AttachmentStore:
    """Thread-safe in-memory attachment store with per-session TTL."""

    def __init__(
        self,
        *,
        ttl_seconds: int = 30 * 60,
        max_per_session: int = 5,
        max_total_bytes_per_session: int = 25 * 1024 * 1024,
    ):
        self._ttl_seconds = ttl_seconds
        self._max_per_session = max_per_session
        self._max_total_bytes_per_session = max_total_bytes_per_session
        # session_id -> {attachment_id -> AttachmentRecord}
        self._by_session: dict[str, dict[str, AttachmentRecord]] = {}
        self._last_touch: dict[str, float] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(
        self,
        *,
        session_id: str,
        filename: str,
        kind: str,
        mime: str,
        size_bytes: int,
        extracted_text: str,
    ) -> AttachmentRecord:
        """Insert a new record. Raises ``ValueError`` on per-session limits."""
        session_id = self._normalize_session_id(session_id)
        with self._lock:
            self._evict_expired_locked()
            session_records = self._by_session.setdefault(session_id, {})
            if len(session_records) >= self._max_per_session:
                raise ValueError(
                    f"Attachment limit reached ({self._max_per_session} per session)."
                )
            current_bytes = sum(r.size_bytes for r in session_records.values())
            if current_bytes + size_bytes > self._max_total_bytes_per_session:
                raise ValueError(
                    "Session attachment size limit exceeded "
                    f"({self._max_total_bytes_per_session // (1024 * 1024)} MB)."
                )
            record = AttachmentRecord(
                attachment_id=uuid.uuid4().hex,
                session_id=session_id,
                filename=filename,
                kind=kind,
                mime=mime,
                size_bytes=size_bytes,
                extracted_text=extracted_text,
            )
            session_records[record.attachment_id] = record
            self._last_touch[session_id] = time.time()
            logger.info(
                "attachment_added session_id=%s attachment_id=%s kind=%s size=%s",
                session_id,
                record.attachment_id,
                kind,
                size_bytes,
            )
            return record

    def get_many(self, session_id: str, attachment_ids: Iterable[str]) -> list[AttachmentRecord]:
        """Return records matching the requested ids, silently skipping unknowns.

        Missing ids are common (e.g. TTL evicted since upload) and are logged
        as warnings but never raise, so a stale client id never blocks a turn.
        """
        session_id = self._normalize_session_id(session_id)
        with self._lock:
            self._evict_expired_locked()
            session_records = self._by_session.get(session_id, {})
            if session_records:
                self._last_touch[session_id] = time.time()
            found: list[AttachmentRecord] = []
            for aid in attachment_ids:
                record = session_records.get(aid)
                if record is None:
                    logger.warning(
                        "attachment_missing session_id=%s attachment_id=%s "
                        "(unknown or evicted)",
                        session_id,
                        aid,
                    )
                    continue
                found.append(record)
            return found

    def list_session(self, session_id: str) -> list[AttachmentRecord]:
        session_id = self._normalize_session_id(session_id)
        with self._lock:
            self._evict_expired_locked()
            return list(self._by_session.get(session_id, {}).values())

    def delete(self, session_id: str, attachment_id: str) -> bool:
        session_id = self._normalize_session_id(session_id)
        with self._lock:
            session_records = self._by_session.get(session_id, {})
            removed = session_records.pop(attachment_id, None) is not None
            if not session_records:
                self._by_session.pop(session_id, None)
                self._last_touch.pop(session_id, None)
            if removed:
                logger.info(
                    "attachment_deleted session_id=%s attachment_id=%s",
                    session_id,
                    attachment_id,
                )
            return removed

    def clear_session(self, session_id: str) -> int:
        session_id = self._normalize_session_id(session_id)
        with self._lock:
            session_records = self._by_session.pop(session_id, {})
            self._last_touch.pop(session_id, None)
            if session_records:
                logger.info(
                    "attachment_session_cleared session_id=%s count=%s",
                    session_id,
                    len(session_records),
                )
            return len(session_records)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_session_id(session_id: str) -> str:
        if not session_id or not isinstance(session_id, str):
            raise ValueError("session_id is required")
        # Keep session_ids short + non-pathological. Client sends a UUID.
        normalized = session_id.strip()
        if len(normalized) > 128:
            raise ValueError("session_id is too long")
        return normalized

    def _evict_expired_locked(self) -> None:
        cutoff = time.time() - self._ttl_seconds
        stale = [sid for sid, ts in self._last_touch.items() if ts < cutoff]
        for sid in stale:
            evicted = self._by_session.pop(sid, {})
            self._last_touch.pop(sid, None)
            if evicted:
                logger.info(
                    "attachment_session_evicted_ttl session_id=%s count=%s",
                    sid,
                    len(evicted),
                )
