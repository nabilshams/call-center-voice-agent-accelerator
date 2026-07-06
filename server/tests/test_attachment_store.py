"""Unit tests for ``app.handler.attachment_store``.

Focus on the invariants that keep the store from leaking memory or serving
stale files: TTL eviction, per-session caps (count + byte total), and the
delete/clear/get_many semantics that the HTTP layer + Teams bot rely on.
``time.time`` is monkey-patched so the TTL test is deterministic.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.handler import attachment_store as store_mod
from app.handler.attachment_store import AttachmentRecord, AttachmentStore


def _add(store: AttachmentStore, session_id: str = "s1", **overrides) -> AttachmentRecord:
    kwargs = dict(
        session_id=session_id,
        filename="boarding.pdf",
        kind="pdf",
        mime="application/pdf",
        size_bytes=1024,
        extracted_text="Passenger: Ada Lovelace",
    )
    kwargs.update(overrides)
    return store.add(**kwargs)


class AddAndListTests(unittest.TestCase):
    def test_add_returns_record_with_uuid_id(self):
        store = AttachmentStore()
        record = _add(store)

        self.assertEqual(record.session_id, "s1")
        self.assertEqual(record.filename, "boarding.pdf")
        self.assertEqual(record.kind, "pdf")
        self.assertEqual(record.size_bytes, 1024)
        self.assertRegex(record.attachment_id, r"^[0-9a-f]{32}$")

    def test_list_session_returns_all_records_for_session(self):
        store = AttachmentStore()
        r1 = _add(store)
        r2 = _add(store, filename="hotel.pdf")

        records = store.list_session("s1")

        self.assertEqual({r.attachment_id for r in records}, {r1.attachment_id, r2.attachment_id})

    def test_sessions_are_isolated(self):
        store = AttachmentStore()
        _add(store, session_id="alice")
        _add(store, session_id="bob")

        self.assertEqual(len(store.list_session("alice")), 1)
        self.assertEqual(len(store.list_session("bob")), 1)

    def test_public_dict_hides_extracted_text(self):
        store = AttachmentStore()
        record = _add(store, extracted_text="Booking ref ABC123 -- confidential")

        public = record.to_public_dict()

        self.assertNotIn("extracted_text", public)
        self.assertNotIn("ABC123", str(public))
        self.assertTrue(public["has_text"])

    def test_public_dict_has_text_false_for_stub(self):
        store = AttachmentStore()
        record = _add(store, extracted_text="[PDF 'boarding.pdf' uploaded but ...]")

        self.assertFalse(record.to_public_dict()["has_text"])


class SessionLimitTests(unittest.TestCase):
    def test_per_session_count_cap_enforced(self):
        store = AttachmentStore(max_per_session=2)
        _add(store)
        _add(store)

        with self.assertRaises(ValueError) as cm:
            _add(store)
        self.assertIn("Attachment limit reached", str(cm.exception))

    def test_per_session_byte_cap_enforced(self):
        # 3 MB cap, first record uses 2 MB, second (2 MB) should fail.
        store = AttachmentStore(max_total_bytes_per_session=3 * 1024 * 1024)
        _add(store, size_bytes=2 * 1024 * 1024)

        with self.assertRaises(ValueError) as cm:
            _add(store, size_bytes=2 * 1024 * 1024)
        self.assertIn("size limit exceeded", str(cm.exception))

    def test_other_sessions_unaffected_by_full_session(self):
        store = AttachmentStore(max_per_session=1)
        _add(store, session_id="alice")

        # Bob still has full quota.
        record = _add(store, session_id="bob")
        self.assertEqual(record.session_id, "bob")

    def test_missing_session_id_rejected(self):
        store = AttachmentStore()

        with self.assertRaises(ValueError):
            _add(store, session_id="")

    def test_oversize_session_id_rejected(self):
        store = AttachmentStore()

        with self.assertRaises(ValueError):
            _add(store, session_id="x" * 129)


class DeleteAndClearTests(unittest.TestCase):
    def test_delete_removes_single_record(self):
        store = AttachmentStore()
        r1 = _add(store)
        r2 = _add(store, filename="hotel.pdf")

        removed = store.delete("s1", r1.attachment_id)

        self.assertTrue(removed)
        remaining = {r.attachment_id for r in store.list_session("s1")}
        self.assertEqual(remaining, {r2.attachment_id})

    def test_delete_unknown_id_returns_false(self):
        store = AttachmentStore()
        _add(store)

        self.assertFalse(store.delete("s1", "does-not-exist"))

    def test_delete_last_record_cleans_up_session_bookkeeping(self):
        store = AttachmentStore()
        r = _add(store)
        store.delete("s1", r.attachment_id)

        # Internal state should not retain empty sessions (memory hygiene).
        self.assertNotIn("s1", store._by_session)  # type: ignore[attr-defined]
        self.assertNotIn("s1", store._last_touch)  # type: ignore[attr-defined]

    def test_clear_session_returns_count_and_empties(self):
        store = AttachmentStore()
        _add(store)
        _add(store, filename="hotel.pdf")

        count = store.clear_session("s1")

        self.assertEqual(count, 2)
        self.assertEqual(store.list_session("s1"), [])

    def test_clear_empty_session_returns_zero(self):
        store = AttachmentStore()
        self.assertEqual(store.clear_session("ghost"), 0)


class GetManyTests(unittest.TestCase):
    def test_returns_records_in_requested_order_skipping_unknowns(self):
        store = AttachmentStore()
        r1 = _add(store)
        r2 = _add(store, filename="hotel.pdf")

        found = store.get_many("s1", [r2.attachment_id, "missing", r1.attachment_id])

        self.assertEqual([r.attachment_id for r in found], [r2.attachment_id, r1.attachment_id])

    def test_returns_empty_for_unknown_session(self):
        store = AttachmentStore()
        self.assertEqual(store.get_many("ghost", ["anything"]), [])


class TtlEvictionTests(unittest.TestCase):
    """Uses ``patch`` on the module-level ``time.time`` symbol so we can
    fast-forward the clock without waiting the real TTL."""

    def test_expired_sessions_are_evicted_on_next_access(self):
        store = AttachmentStore(ttl_seconds=60)

        # t=0 -- add a record
        with patch.object(store_mod.time, "time", return_value=1_000_000.0):
            _add(store)
            self.assertEqual(len(store.list_session("s1")), 1)

        # t=+120s -- eviction cutoff has passed
        with patch.object(store_mod.time, "time", return_value=1_000_120.0):
            self.assertEqual(store.list_session("s1"), [])

    def test_activity_within_ttl_refreshes_touch(self):
        store = AttachmentStore(ttl_seconds=60)

        with patch.object(store_mod.time, "time", return_value=1_000_000.0):
            _add(store)

        # t=+30s -- add a second file; this must refresh last_touch.
        with patch.object(store_mod.time, "time", return_value=1_000_030.0):
            _add(store, filename="hotel.pdf")

        # t=+80s -- 50s since last add (< 60s TTL), still alive.
        with patch.object(store_mod.time, "time", return_value=1_000_080.0):
            self.assertEqual(len(store.list_session("s1")), 2)


if __name__ == "__main__":
    unittest.main()
