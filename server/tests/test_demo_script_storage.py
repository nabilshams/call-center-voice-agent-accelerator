"""Tier-3 tests for the demo-script storage layer.

``DemoScriptStorage`` guards a subtle rule set: the shipped seed at
``app/data/travel_agency_demo_script.json`` must never be mutated; edits
land in a sibling ``*.runtime.json`` file that shadows the seed for
subsequent loads and can be reset by simply deleting it. On top of that,
``_normalize`` coerces arbitrary UI payloads into the canonical
``{sections: [{title, note, lines}]}`` shape, dropping garbage silently.

Every test uses a fresh temporary runtime path via ``tempfile.TemporaryDirectory``
so we never touch the real repo copy on disk.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.handler.demo_script_storage import DemoScriptStorage


class _StorageTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.runtime_path = Path(self._tmp.name) / "runtime.json"
        self.storage = DemoScriptStorage(runtime_path=str(self.runtime_path))

    def tearDown(self):
        self._tmp.cleanup()


# =========================================================================
# _normalize -- the coercion contract
# =========================================================================


class NormalizeTests(_StorageTestBase):
    """Guard the coercion rules that convert arbitrary UI JSON into the
    canonical `{sections: [{title, note, lines}]}` shape. A bug here would
    let malformed payloads survive save/load and break the client renderer."""

    def test_missing_sections_key_returns_empty(self):
        self.assertEqual(DemoScriptStorage._normalize({}), {"sections": []})

    def test_non_list_sections_returns_empty(self):
        self.assertEqual(
            DemoScriptStorage._normalize({"sections": "not a list"}),
            {"sections": []},
        )

    def test_non_dict_input_returns_empty(self):
        self.assertEqual(DemoScriptStorage._normalize([]), {"sections": []})
        self.assertEqual(DemoScriptStorage._normalize("string"), {"sections": []})

    def test_non_dict_sections_are_dropped(self):
        result = DemoScriptStorage._normalize({"sections": ["string", 42, None]})
        self.assertEqual(result, {"sections": []})

    def test_valid_section_is_preserved(self):
        result = DemoScriptStorage._normalize({
            "sections": [
                {"title": "Intro", "note": "n", "lines": ["a", "b"]},
            ]
        })
        self.assertEqual(result, {
            "sections": [{"title": "Intro", "note": "n", "lines": ["a", "b"]}],
        })

    def test_title_and_note_are_stripped(self):
        result = DemoScriptStorage._normalize({
            "sections": [{"title": "  Padded  ", "note": "\tspacy\n", "lines": []}]
        })
        self.assertEqual(result["sections"][0]["title"], "Padded")
        self.assertEqual(result["sections"][0]["note"], "spacy")

    def test_lines_are_coerced_from_numerics(self):
        # ints and floats are stringified; the code accepts (str, int, float).
        result = DemoScriptStorage._normalize({
            "sections": [{"title": "T", "lines": [1, 2.5, "text"]}]
        })
        self.assertEqual(result["sections"][0]["lines"], ["1", "2.5", "text"])

    def test_lines_drop_blank_and_non_scalar(self):
        result = DemoScriptStorage._normalize({
            "sections": [{"title": "T", "lines": ["", "  ", None, {"a": 1}, ["nested"], "keep"]}]
        })
        self.assertEqual(result["sections"][0]["lines"], ["keep"])

    def test_missing_title_note_lines_defaults_are_empty(self):
        # Section with only a note survives; entirely empty section is dropped.
        result = DemoScriptStorage._normalize({
            "sections": [
                {"note": "only a note"},
                {},   # entirely empty -> dropped
                {"title": "", "note": "", "lines": []},  # empty after coercion -> dropped
            ]
        })
        self.assertEqual(len(result["sections"]), 1)
        self.assertEqual(result["sections"][0]["note"], "only a note")

    def test_normalize_output_shape_contract(self):
        # Every returned section has exactly these three keys.
        result = DemoScriptStorage._normalize({
            "sections": [{"title": "T", "note": "N", "lines": ["a"]}, {"extra": "ignored", "title": "X"}]
        })
        for section in result["sections"]:
            self.assertEqual(set(section.keys()), {"title", "note", "lines"})


# =========================================================================
# load() / save() / reset() lifecycle
# =========================================================================


class LifecycleTests(_StorageTestBase):
    def test_load_with_no_runtime_falls_back_to_seed(self):
        # Fresh storage with a runtime path that doesn't exist. The seed
        # ships with the repo -- verify it is loaded.
        self.assertFalse(self.runtime_path.exists())
        data = self.storage.load()
        self.assertIsInstance(data, dict)
        self.assertIsInstance(data["sections"], list)
        self.assertGreater(
            len(data["sections"]),
            0,
            "shipped seed at app/data/travel_agency_demo_script.json should have sections",
        )

    def test_save_writes_normalized_runtime_file(self):
        # Save some raw input with padding + noise; verify normalization
        # is applied both to the returned value and to the on-disk file.
        raw = {"sections": [{"title": "  Padded  ", "note": "", "lines": [1, "", "x"]}]}
        returned = self.storage.save(raw)

        # Returned value is normalized.
        self.assertEqual(returned["sections"][0]["title"], "Padded")
        self.assertEqual(returned["sections"][0]["lines"], ["1", "x"])

        # And the runtime file on disk matches (JSON round-trip).
        self.assertTrue(self.runtime_path.exists())
        on_disk = json.loads(self.runtime_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk, returned)

    def test_load_prefers_runtime_over_seed(self):
        # Save something distinctive, then load: it must be what we saved,
        # not the shipped seed.
        self.storage.save({"sections": [{"title": "RuntimeOnly", "note": "", "lines": ["x"]}]})
        loaded = self.storage.load()
        self.assertEqual(len(loaded["sections"]), 1)
        self.assertEqual(loaded["sections"][0]["title"], "RuntimeOnly")

    def test_reset_deletes_runtime_and_reloads_seed(self):
        # Save runtime, verify it wins on load, then reset -> seed wins.
        self.storage.save({"sections": [{"title": "TempOverride", "note": "", "lines": []}]})
        self.assertTrue(self.runtime_path.exists())

        seed_after_reset = self.storage.reset()
        self.assertFalse(self.runtime_path.exists())
        # Seed should not contain our override title.
        titles = {s["title"] for s in seed_after_reset["sections"]}
        self.assertNotIn("TempOverride", titles)

    def test_reset_is_idempotent_when_runtime_missing(self):
        # No runtime yet; reset should still succeed and return the seed.
        self.assertFalse(self.runtime_path.exists())
        result = self.storage.reset()
        self.assertIsInstance(result, dict)
        self.assertIn("sections", result)

    def test_corrupt_runtime_is_ignored_and_seed_wins(self):
        # Write garbage into the runtime file; load() should log and fall
        # through to the shipped seed rather than raising.
        self.runtime_path.write_text("{ not valid json", encoding="utf-8")
        loaded = self.storage.load()
        self.assertIsInstance(loaded, dict)
        # Falls back to seed which has at least one section.
        self.assertGreater(len(loaded["sections"]), 0)

    def test_runtime_missing_sections_key_falls_through_to_seed(self):
        # Even a syntactically valid runtime file that doesn't match the
        # expected shape is skipped in favour of the seed.
        self.runtime_path.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
        loaded = self.storage.load()
        self.assertGreater(len(loaded["sections"]), 0)

    def test_save_creates_missing_parent_directory(self):
        # Point at a nested path that does not exist; save must mkdir -p.
        nested = self.runtime_path.parent / "deep" / "nested" / "script.json"
        storage = DemoScriptStorage(runtime_path=str(nested))
        storage.save({"sections": [{"title": "T", "note": "", "lines": []}]})
        self.assertTrue(nested.exists())


if __name__ == "__main__":
    unittest.main()
