"""Unit tests for the Foundry AgentDefinition loader (prompt kind)."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from app.foundry import (
    AgentDefinitionError,
    PromptAgentInfo,
    PromptAgentSpec,
    discover_definitions,
    load_definition,
    spec_matches_existing,
)


def _write(path: Path, contents: str) -> Path:
    path.write_text(contents, encoding="utf-8")
    return path


_MINIMAL = "name: A\nkind: prompt\nmodel: gpt-4o-mini\n"


class LoadDefinitionTests(unittest.TestCase):
    def test_loads_minimal_definition(self):
        with TemporaryDirectory() as tmp:
            m = _write(Path(tmp) / "minimal.yaml", _MINIMAL)
            loaded = load_definition(m)
        self.assertEqual(loaded.spec.name, "A")
        self.assertEqual(loaded.spec.model, "gpt-4o-mini")
        self.assertIsNone(loaded.spec.instructions)
        self.assertEqual(loaded.spec.tools, [])
        self.assertEqual(loaded.spec.metadata, {})

    def test_expands_env_vars(self):
        with TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"MAF_MODEL": "gpt-4o-mini"}, clear=False
        ):
            m = _write(
                Path(tmp) / "a.yaml",
                'name: A\nkind: prompt\nmodel: "${MAF_MODEL}"\n',
            )
            loaded = load_definition(m)
        self.assertEqual(loaded.spec.model, "gpt-4o-mini")

    def test_missing_env_var_raises(self):
        with TemporaryDirectory() as tmp:
            m = _write(
                Path(tmp) / "a.yaml",
                'name: A\nkind: prompt\nmodel: "${DEFINITELY_NOT_SET_XYZ}"\n',
            )
            with self.assertRaises(AgentDefinitionError):
                load_definition(m)

    def test_instructions_file_is_loaded(self):
        with TemporaryDirectory() as tmp:
            prompts = Path(tmp) / "prompts"
            prompts.mkdir()
            _write(prompts / "hello.md", "Hello there\n")
            m = _write(
                Path(tmp) / "a.yaml",
                _MINIMAL + "instructions_file: prompts/hello.md\n",
            )
            loaded = load_definition(m)
        self.assertEqual(loaded.spec.instructions, "Hello there\n")

    def test_both_instructions_and_file_rejected(self):
        with TemporaryDirectory() as tmp:
            m = _write(
                Path(tmp) / "a.yaml",
                _MINIMAL + "instructions: inline\ninstructions_file: x.md\n",
            )
            with self.assertRaises(AgentDefinitionError):
                load_definition(m)

    def test_missing_name_kind_or_model_rejected(self):
        with TemporaryDirectory() as tmp:
            m1 = _write(Path(tmp) / "no-name.yaml", "kind: prompt\nmodel: m\n")
            m2 = _write(Path(tmp) / "no-kind.yaml", "name: A\nmodel: m\n")
            m3 = _write(Path(tmp) / "no-model.yaml", "name: A\nkind: prompt\n")
            for m in (m1, m2, m3):
                with self.assertRaises(AgentDefinitionError):
                    load_definition(m)

    def test_unsupported_kind_rejected(self):
        with TemporaryDirectory() as tmp:
            m = _write(
                Path(tmp) / "a.yaml",
                "name: A\nkind: hosted\nmodel: m\n",
            )
            with self.assertRaises(AgentDefinitionError):
                load_definition(m)

    def test_tools_must_be_list_of_mappings(self):
        with TemporaryDirectory() as tmp:
            m = _write(Path(tmp) / "a.yaml", _MINIMAL + "tools: [not-a-dict]\n")
            with self.assertRaises(AgentDefinitionError):
                load_definition(m)

    def test_metadata_must_be_string_to_string(self):
        with TemporaryDirectory() as tmp:
            m = _write(Path(tmp) / "a.yaml", _MINIMAL + "metadata:\n  k: 42\n")
            with self.assertRaises(AgentDefinitionError):
                load_definition(m)

    def test_top_level_must_be_mapping(self):
        with TemporaryDirectory() as tmp:
            m = _write(Path(tmp) / "a.yaml", "- just\n- a\n- list\n")
            with self.assertRaises(AgentDefinitionError):
                load_definition(m)


class DiscoverDefinitionsTests(unittest.TestCase):
    def test_discovers_flat_files_sorted(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "b.yaml", "name: B\nkind: prompt\nmodel: m\n")
            _write(root / "a.yaml", "name: A\nkind: prompt\nmodel: m\n")
            _write(root / "ignored.txt", "nope\n")
            loaded = discover_definitions([root])
        self.assertEqual([m.spec.name for m in loaded], ["A", "B"])

    def test_discovers_per_agent_folders(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "one").mkdir()
            (root / "two").mkdir()
            _write(root / "one" / "agent.yaml", "name: One\nkind: prompt\nmodel: m\n")
            _write(root / "two" / "agent.yaml", "name: Two\nkind: prompt\nmodel: m\n")
            loaded = discover_definitions([root])
        self.assertEqual({m.spec.name for m in loaded}, {"One", "Two"})

    def test_flat_and_per_agent_coexist_without_duplicates(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "flat.yaml", "name: Flat\nkind: prompt\nmodel: m\n")
            (root / "nested").mkdir()
            _write(root / "nested" / "agent.yaml", "name: Nested\nkind: prompt\nmodel: m\n")
            loaded = discover_definitions([root])
        names = [m.spec.name for m in loaded]
        self.assertEqual(sorted(names), ["Flat", "Nested"])
        self.assertEqual(len(names), 2)

    def test_missing_path_raises(self):
        with self.assertRaises(AgentDefinitionError):
            discover_definitions(["/definitely/does/not/exist/xyz"])

    def test_non_prompt_kinds_are_filtered_by_default(self):
        # Hosted agent definitions co-located under agents/ must not break
        # prompt-only tooling like apply_prompt_agents.py.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "prompt_one").mkdir()
            (root / "hosted_one").mkdir()
            _write(root / "prompt_one" / "agent.yaml", "name: P\nkind: prompt\nmodel: m\n")
            _write(
                root / "hosted_one" / "agent.yaml",
                "name: H\nkind: hosted\nprotocols:\n  - protocol: responses\n    version: 1.0.0\n",
            )
            loaded = discover_definitions([root])
        self.assertEqual([m.spec.name for m in loaded], ["P"])

    def test_yaml_without_kind_is_silently_skipped(self):
        # Co-located eval.yaml / dataset config files (no ``kind:``) sit
        # next to agent.yaml files under agents/<snake>/. Directory
        # discovery must skip them silently rather than fail loading.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent_dir = root / "flight_booking"
            agent_dir.mkdir()
            _write(agent_dir / "agent.yaml", "name: FlightBookingAgent\nkind: prompt\nmodel: m\n")
            _write(
                agent_dir / "eval.yaml",
                "name: flight-booking-smoke\nagent:\n  name: FlightBookingAgent\n  kind: prompt\n",
            )
            loaded = discover_definitions([root])
        self.assertEqual([m.spec.name for m in loaded], ["FlightBookingAgent"])


class SpecMatchesExistingTests(unittest.TestCase):
    def _spec(self, **overrides) -> PromptAgentSpec:
        base = dict(
            name="A",
            model="gpt-4o-mini",
            instructions="hi",
            description="desc",
            temperature=0.2,
            tools=[],
            metadata={"k": "v"},
        )
        base.update(overrides)
        return PromptAgentSpec(**base)

    def _info(self, **overrides) -> PromptAgentInfo:
        base = dict(
            name="A",
            version="1",
            id="A:1",
            model="gpt-4o-mini",
            instructions="hi",
            description="desc",
            tools=[],
            metadata={"k": "v"},
            status="active",
            created_at="0",
            temperature=0.2,
            top_p=None,
        )
        base.update(overrides)
        return PromptAgentInfo(**base)

    def test_identical_matches(self):
        self.assertTrue(spec_matches_existing(self._spec(), self._info()))

    def test_trailing_newline_in_instructions_is_ignored(self):
        self.assertTrue(
            spec_matches_existing(
                self._spec(instructions="hi\n"), self._info(instructions="hi")
            )
        )

    def test_different_instructions_detected(self):
        self.assertFalse(
            spec_matches_existing(
                self._spec(instructions="new"), self._info(instructions="old")
            )
        )

    def test_different_model_detected(self):
        self.assertFalse(
            spec_matches_existing(self._spec(model="gpt-4o"), self._info())
        )

    def test_different_temperature_detected(self):
        self.assertFalse(
            spec_matches_existing(
                self._spec(temperature=0.7), self._info(temperature=0.2)
            )
        )

    def test_none_temperature_ignored(self):
        # spec doesn't specify temperature -> live agent's value is authoritative
        self.assertTrue(
            spec_matches_existing(
                self._spec(temperature=None), self._info(temperature=0.9)
            )
        )

    def test_metadata_drift_detected(self):
        self.assertFalse(
            spec_matches_existing(
                self._spec(metadata={"k": "v2"}), self._info(metadata={"k": "v"})
            )
        )

    def test_tool_drift_detected(self):
        self.assertFalse(
            spec_matches_existing(
                self._spec(tools=[{"type": "code_interpreter"}]),
                self._info(tools=[]),
            )
        )

    def test_portal_only_metadata_keys_ignored(self):
        # Foundry portal injects logo/modified_at/microsoft.* + empty description
        # into the live metadata; those must not count as drift against a spec
        # that only asserts on user-authored keys.
        live_meta = {
            "k": "v",
            "logo": "Avatar_Default.svg",
            "modified_at": "1781248269",
            "microsoft.voice-live.enabled": "false",
            "description": "",
        }
        self.assertTrue(
            spec_matches_existing(
                self._spec(metadata={"k": "v"}),
                self._info(metadata=live_meta),
            )
        )

    def test_per_line_trailing_whitespace_ignored_in_instructions(self):
        # Portal often stores trailing spaces before each newline; our dump
        # strips them. The apply-side matcher must accept both forms.
        self.assertTrue(
            spec_matches_existing(
                self._spec(instructions="line1\nline2\n"),
                self._info(instructions="line1   \nline2  \n"),
            )
        )


if __name__ == "__main__":
    unittest.main()
