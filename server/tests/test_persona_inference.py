"""Tier-3 tests for the persona-inference and recap-inference services.

Both services wrap an Azure OpenAI client to (a) diarize a running speech
transcript into per-speaker turns and (b) generate a downstream summary
(free text for ``PersonaInferenceService``, structured SOAP note for
``RecapInferenceService``).

These tests exercise the deterministic parts -- debounce, bounded memory,
response sanitisation, taxonomy clamping, section validation -- with the
LLM client fully mocked. No network I/O.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.handler.persona_inference import (
    MAX_TRANSCRIPT_CHARS,
    MIN_INFERENCE_INTERVAL_S,
    MIN_NEW_CHARS,
    PersonaInferenceService,
)
from app.handler.recap_inference import (
    SECTION_GUIDANCE,
    SOAP_SECTIONS,
    RecapInferenceService,
)


# =========================================================================
# Helpers -- build a service with a stubbed LLM client
# =========================================================================


_DEFAULT_CONFIG = {
    "AZURE_OPENAI_ENDPOINT": "https://fake.openai.azure.com/",
    "AZURE_OPENAI_CHAT_DEPLOYMENT": "gpt-4o",
    "AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID": "client-id-not-used-in-tests",
}


def _make_llm_response(payload: dict) -> SimpleNamespace:
    """Construct the shape returned by ``openai.AsyncAzureOpenAI.chat.completions.create``."""
    message = SimpleNamespace(content=json.dumps(payload))
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def _make_service(
    cls=PersonaInferenceService,
    config: dict | None = None,
    sender=None,
):
    sender = sender or MagicMock()
    svc = cls(config or _DEFAULT_CONFIG, sender)
    # Stub the LLM client so `_ensure_client` becomes a no-op.
    svc._client = MagicMock()
    svc._client.chat = MagicMock()
    svc._client.chat.completions = MagicMock()
    svc._client.chat.completions.create = AsyncMock()
    return svc, sender


# =========================================================================
# Enabled/disabled construction
# =========================================================================


class ConstructionTests(unittest.TestCase):
    def test_missing_endpoint_disables_service(self):
        svc = PersonaInferenceService(
            {"AZURE_OPENAI_CHAT_DEPLOYMENT": "gpt-4o"}, MagicMock()
        )
        self.assertFalse(svc._enabled)

    def test_missing_deployment_disables_service(self):
        svc = PersonaInferenceService(
            {"AZURE_OPENAI_ENDPOINT": "https://x.openai.azure.com/"}, MagicMock()
        )
        self.assertFalse(svc._enabled)

    def test_both_endpoint_and_deployment_enable_service(self):
        svc = PersonaInferenceService(_DEFAULT_CONFIG, MagicMock())
        self.assertTrue(svc._enabled)

    def test_endpoint_trailing_slash_is_stripped(self):
        svc = PersonaInferenceService(_DEFAULT_CONFIG, MagicMock())
        self.assertFalse(svc._endpoint.endswith("/"))


# =========================================================================
# add_segment -- accumulation, dedup, bounded memory
# =========================================================================


class AddSegmentTests(unittest.TestCase):
    def test_disabled_service_ignores_segments(self):
        svc = PersonaInferenceService({}, MagicMock())
        self.assertFalse(svc._enabled)
        svc.add_segment("Speaker 1", "hello world")
        self.assertEqual(svc._segments, [])

    def test_empty_text_is_ignored(self):
        svc, _ = _make_service()
        svc.add_segment("Speaker 1", "")
        svc.add_segment("Speaker 1", "   \n\t  ")
        self.assertEqual(svc._segments, [])
        self.assertEqual(svc._segments_chars, 0)

    def test_segments_are_stripped_and_accumulated(self):
        svc, _ = _make_service()
        svc.add_segment("s", "  hello  ")
        svc.add_segment("s", "world")
        self.assertEqual(svc._segments, ["hello", "world"])
        # Chars = len("hello")+1 + len("world")+1 = 12.
        self.assertEqual(svc._segments_chars, 12)

    def test_memory_bound_drops_oldest_when_over_limit(self):
        svc, _ = _make_service()
        chunk = "x" * 500  # +1 for the char accounting
        # Feed enough segments to blow past MAX_TRANSCRIPT_CHARS by a wide
        # margin; the deque-style trimming should keep memory bounded.
        for _ in range(30):
            svc.add_segment("s", chunk)
        self.assertLessEqual(svc._segments_chars, MAX_TRANSCRIPT_CHARS + len(chunk))
        # And the segments list must have shrunk correspondingly.
        self.assertLess(len(svc._segments), 30)
        self.assertGreaterEqual(len(svc._segments), 1)

    def test_memory_bound_keeps_at_least_one_segment(self):
        svc, _ = _make_service()
        # A single segment larger than the limit is still retained (loop
        # guard: `len(self._segments) > 1`).
        huge = "y" * (MAX_TRANSCRIPT_CHARS * 2)
        svc.add_segment("s", huge)
        self.assertEqual(len(svc._segments), 1)


# =========================================================================
# Debounce / inflight guards in _maybe_run
# =========================================================================


class MaybeRunGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_inflight_blocks_second_call(self):
        svc, _ = _make_service()
        svc._inflight = True
        svc._segments = ["x" * 200]
        svc._segments_chars = 200
        svc._last_inferred_chars = 0
        svc._last_run_at = 0.0
        # Even though there is plenty of new content and cooldown passed,
        # inflight guard must return early WITHOUT calling the client.
        await svc._maybe_run()
        svc._client.chat.completions.create.assert_not_called()

    async def test_cooldown_blocks_second_call(self):
        import time

        svc, _ = _make_service()
        svc._segments = ["x" * (MIN_NEW_CHARS * 5)]
        svc._segments_chars = MIN_NEW_CHARS * 5
        svc._last_inferred_chars = 0
        # Freshly set last_run_at => cooldown hasn't elapsed.
        svc._last_run_at = time.monotonic()
        await svc._maybe_run()
        svc._client.chat.completions.create.assert_not_called()

    async def test_insufficient_new_chars_blocks_call(self):
        svc, _ = _make_service()
        svc._segments = ["short"]
        svc._segments_chars = 5
        svc._last_inferred_chars = 0
        svc._last_run_at = 0.0  # cooldown definitely elapsed
        self.assertLess(svc._segments_chars - svc._last_inferred_chars, MIN_NEW_CHARS)
        await svc._maybe_run()
        svc._client.chat.completions.create.assert_not_called()

    async def test_all_gates_pass_invokes_run_once(self):
        svc, _ = _make_service()
        svc._segments = ["x" * (MIN_NEW_CHARS * 5)]
        svc._segments_chars = MIN_NEW_CHARS * 5
        svc._last_inferred_chars = 0
        svc._last_run_at = 0.0
        # Also short-circuit _run_once so we don't drive a full LLM cycle here.
        called = asyncio.Event()

        async def _fake_run_once():
            called.set()

        svc._run_once = _fake_run_once  # type: ignore[assignment]
        await svc._maybe_run()
        self.assertTrue(called.is_set())
        # And the inflight flag must be cleared afterwards.
        self.assertFalse(svc._inflight)


# =========================================================================
# _run_once -- response parsing + sanitisation
# =========================================================================


class RunOnceParsingTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_response_dispatches_dialogue_message(self):
        svc, sender = _make_service()
        svc._segments = ["hello there. hi how are you"]
        svc._segments_chars = 27
        svc._client.chat.completions.create.return_value = _make_llm_response({
            "speakers": {
                "Speaker 1": {"persona": "Agent", "confidence": "HIGH", "rationale": "greeting"},
                "Speaker 2": {"persona": "Customer", "confidence": "medium", "rationale": ""},
            },
            "turns": [
                {"speaker": "Speaker 1", "text": "hello there"},
                {"speaker": "Speaker 2", "text": "hi how are you"},
            ],
        })
        await svc._run_once()
        sender.assert_called_once()
        payload = sender.call_args.args[0]
        self.assertEqual(payload["type"], "dialogue")
        self.assertEqual(len(payload["turns"]), 2)
        self.assertEqual(payload["personas"]["Speaker 1"]["persona"], "Agent")
        # Confidence is lower-cased.
        self.assertEqual(payload["personas"]["Speaker 1"]["confidence"], "high")

    async def test_non_json_response_is_swallowed_not_raised(self):
        svc, sender = _make_service()
        svc._segments = ["hello world"]
        svc._segments_chars = 11
        # Return literal garbage; sanitiser must log and return quietly.
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="not-json-at-all"))]
        )
        svc._client.chat.completions.create.return_value = response
        await svc._run_once()
        sender.assert_not_called()

    async def test_client_exception_is_swallowed(self):
        svc, sender = _make_service()
        svc._segments = ["hello world"]
        svc._segments_chars = 11
        svc._client.chat.completions.create.side_effect = RuntimeError("network down")
        # Must not propagate -- the background task should keep running.
        await svc._run_once()
        sender.assert_not_called()

    async def test_malformed_turns_and_personas_are_filtered(self):
        svc, sender = _make_service()
        svc._segments = ["hi"]
        svc._segments_chars = 2
        svc._client.chat.completions.create.return_value = _make_llm_response({
            "speakers": {
                "Speaker 1": "not a dict, should be dropped",
                "Speaker 2": {"persona": "Agent"},  # missing confidence -> defaulted to 'low'
            },
            "turns": [
                {"speaker": "Speaker 1"},         # no text -> dropped
                {"text": "orphan"},               # no speaker -> dropped
                "just a string",                  # not a dict -> dropped
                {"speaker": "Speaker 2", "text": "hi"},
            ],
        })
        await svc._run_once()
        payload = sender.call_args.args[0]
        # Only one valid turn survived; only one valid persona survived.
        self.assertEqual(payload["turns"], [{"speaker": "Speaker 2", "text": "hi"}])
        self.assertEqual(list(payload["personas"].keys()), ["Speaker 2"])
        # Missing confidence defaults to 'low'.
        self.assertEqual(payload["personas"]["Speaker 2"]["confidence"], "low")

    async def test_empty_transcript_short_circuits(self):
        svc, sender = _make_service()
        svc._segments = []
        svc._segments_chars = 0
        await svc._run_once()
        svc._client.chat.completions.create.assert_not_called()
        sender.assert_not_called()


# =========================================================================
# summarize() -- happy path + fallbacks
# =========================================================================


class SummarizeTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_service_returns_none(self):
        svc = PersonaInferenceService({}, MagicMock())
        self.assertIsNone(await svc.summarize())

    async def test_summarize_uses_last_turns_when_available(self):
        svc, _ = _make_service()
        svc._last_turns = [
            {"speaker": "Speaker 1", "text": "hi"},
            {"speaker": "Speaker 2", "text": "hello back"},
        ]
        svc._client.chat.completions.create.return_value = _make_llm_response({
            "summary": "short chat",
            "key_points": ["greeting"],
            "action_items": [],
        })
        # We inspect the transcript that was sent to the LLM.
        result = await svc.summarize()
        self.assertIsNotNone(result)
        # Verify the transcript passed to the model was built from _last_turns
        # (one line per turn, "Speaker: text" format).
        _, kwargs = svc._client.chat.completions.create.call_args
        user_msg = kwargs["messages"][1]["content"]
        self.assertIn("Speaker 1: hi", user_msg)
        self.assertIn("Speaker 2: hello back", user_msg)

    async def test_summarize_falls_back_to_raw_segments_when_no_turns(self):
        svc, _ = _make_service()
        svc._last_turns = []
        svc._segments = ["one two", "three four"]
        svc._client.chat.completions.create.return_value = _make_llm_response({
            "summary": "s",
        })
        await svc.summarize()
        _, kwargs = svc._client.chat.completions.create.call_args
        user_msg = kwargs["messages"][1]["content"]
        # Raw segments joined with a space.
        self.assertIn("one two three four", user_msg)

    async def test_summarize_returns_none_when_transcript_empty(self):
        svc, _ = _make_service()
        svc._last_turns = []
        svc._segments = []
        self.assertIsNone(await svc.summarize())


# =========================================================================
# RecapInferenceService -- clinical taxonomy + SOAP
# =========================================================================


class RecapConstantsTests(unittest.TestCase):
    def test_soap_sections_are_five_and_in_order(self):
        self.assertEqual(
            SOAP_SECTIONS,
            ("chief_complaint", "subjective", "objective", "assessment", "plan"),
        )

    def test_every_soap_section_has_guidance(self):
        # A missing guidance entry would silently ship an empty prompt to the model.
        for name in SOAP_SECTIONS:
            self.assertIn(name, SECTION_GUIDANCE)
            self.assertGreater(len(SECTION_GUIDANCE[name]), 0)

    def test_diarization_prompt_bakes_in_clinical_taxonomy(self):
        svc, _ = _make_service(cls=RecapInferenceService)
        msg = svc._build_diarization_system_msg()
        self.assertIn("Clinician", msg)
        self.assertIn("Patient", msg)
        self.assertIn("Other", msg)


class RecapDiarizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_persona_is_clamped_to_other(self):
        svc, sender = _make_service(cls=RecapInferenceService)
        svc._segments = ["some clinical dialogue"]
        svc._segments_chars = 22
        svc._client.chat.completions.create.return_value = _make_llm_response({
            "speakers": {
                "Speaker 1": {"persona": "Alien", "confidence": "high", "rationale": ""},
                "Speaker 2": {"persona": "Clinician", "confidence": "high", "rationale": ""},
                "Speaker 3": {"persona": "Patient", "confidence": "low", "rationale": ""},
            },
            "turns": [
                {"speaker": "Speaker 1", "text": "chirp"},
                {"speaker": "Speaker 2", "text": "how are you feeling?"},
                {"speaker": "Speaker 3", "text": "not great"},
            ],
        })
        await svc._run_once()
        payload = sender.call_args.args[0]
        # Alien -> Other; Clinician/Patient preserved verbatim.
        self.assertEqual(payload["personas"]["Speaker 1"]["persona"], "Other")
        self.assertEqual(payload["personas"]["Speaker 2"]["persona"], "Clinician")
        self.assertEqual(payload["personas"]["Speaker 3"]["persona"], "Patient")

    async def test_role_for_speaker_uses_persona_map(self):
        svc, _ = _make_service(cls=RecapInferenceService)
        svc._last_personas = {"Speaker 1": {"persona": "Clinician"}}
        self.assertEqual(svc._role_for_speaker("Speaker 1"), "Clinician")

    async def test_role_for_speaker_falls_back_to_label(self):
        svc, _ = _make_service(cls=RecapInferenceService)
        self.assertEqual(svc._role_for_speaker("Speaker 9"), "Speaker 9")


class RecapSummarizeTests(unittest.IsolatedAsyncioTestCase):
    async def test_summarize_returns_soap_sections(self):
        svc, _ = _make_service(cls=RecapInferenceService)
        svc._segments = ["patient reports headache"]
        svc._segments_chars = 24
        svc._client.chat.completions.create.return_value = _make_llm_response({
            "sections": {
                "chief_complaint": {"text": "Headache", "confidence": "high"},
                "subjective": {"text": "Onset 3 days ago", "confidence": "MEDIUM"},
                "objective": {"text": "Vitals normal", "confidence": "not-a-level"},
                # missing assessment (not in payload at all)
                "plan": {"text": "Rest, hydration", "confidence": "high"},
            }
        })
        result = await svc.summarize()
        self.assertIsNotNone(result)
        sections = result["sections"]
        self.assertEqual(sections["chief_complaint"]["confidence"], "high")
        # Case-insensitive normalisation of confidence.
        self.assertEqual(sections["subjective"]["confidence"], "medium")
        # Invalid confidence forced to 'low'.
        self.assertEqual(sections["objective"]["confidence"], "low")
        # Missing section is materialised as an empty low-confidence placeholder
        # so the UI always renders all five SOAP sections (blanks are visible
        # to the clinician rather than silently omitted).
        self.assertIn("assessment", sections)
        self.assertEqual(sections["assessment"], {"text": "", "confidence": "low"})

    async def test_summarize_returns_none_when_no_sections_valid(self):
        svc, _ = _make_service(cls=RecapInferenceService)
        svc._segments = ["hi"]
        svc._segments_chars = 2
        svc._client.chat.completions.create.return_value = _make_llm_response({
            "sections": "not a dict",
        })
        self.assertIsNone(await svc.summarize())


class RecapGenerateSectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_section_name_returns_none_without_llm_call(self):
        svc, _ = _make_service(cls=RecapInferenceService)
        result = await svc.generate_section("bogus_section")
        self.assertIsNone(result)
        svc._client.chat.completions.create.assert_not_called()

    async def test_disabled_service_returns_none(self):
        svc = RecapInferenceService({}, MagicMock())
        self.assertIsNone(await svc.generate_section("chief_complaint"))

    async def test_empty_transcript_returns_none(self):
        svc, _ = _make_service(cls=RecapInferenceService)
        # No segments and no last_turns -> transcript is empty.
        svc._segments = []
        svc._last_turns = []
        result = await svc.generate_section("plan")
        self.assertIsNone(result)

    async def test_happy_path_returns_normalised_section(self):
        svc, _ = _make_service(cls=RecapInferenceService)
        svc._segments = ["dialogue here"]
        svc._client.chat.completions.create.return_value = _make_llm_response({
            "text": "  regenerated section body  ",
            "confidence": "HIGH",
        })
        result = await svc.generate_section("assessment")
        self.assertEqual(result["section"], "assessment")
        self.assertEqual(result["text"], "regenerated section body")
        self.assertEqual(result["confidence"], "high")

    async def test_invalid_confidence_is_clamped_to_low(self):
        svc, _ = _make_service(cls=RecapInferenceService)
        svc._segments = ["dialogue"]
        svc._client.chat.completions.create.return_value = _make_llm_response({
            "text": "body",
            "confidence": "extremely-high",
        })
        result = await svc.generate_section("plan")
        self.assertEqual(result["confidence"], "low")

    async def test_empty_text_returns_none(self):
        svc, _ = _make_service(cls=RecapInferenceService)
        svc._segments = ["dialogue"]
        svc._client.chat.completions.create.return_value = _make_llm_response({
            "text": "",
            "confidence": "high",
        })
        self.assertIsNone(await svc.generate_section("plan"))

    async def test_transcript_override_is_honoured(self):
        svc, _ = _make_service(cls=RecapInferenceService)
        svc._segments = ["should not be used"]
        svc._client.chat.completions.create.return_value = _make_llm_response({
            "text": "body", "confidence": "high",
        })
        await svc.generate_section("plan", transcript_text="OVERRIDE_TRANSCRIPT")
        _, kwargs = svc._client.chat.completions.create.call_args
        user_msg = kwargs["messages"][1]["content"]
        self.assertIn("OVERRIDE_TRANSCRIPT", user_msg)
        self.assertNotIn("should not be used", user_msg)


# =========================================================================
# Sanity: tuning constants haven't drifted silently
# =========================================================================


class TuningConstantsTests(unittest.TestCase):
    def test_min_inference_interval_is_positive(self):
        self.assertGreater(MIN_INFERENCE_INTERVAL_S, 0.0)

    def test_min_new_chars_is_reasonable(self):
        # Less than 10 chars would fire the LLM on almost every keystroke;
        # more than 500 would miss short exchanges.
        self.assertGreaterEqual(MIN_NEW_CHARS, 10)
        self.assertLessEqual(MIN_NEW_CHARS, 500)

    def test_max_transcript_chars_is_reasonable(self):
        # 1k-64k covers all plausible token budgets while keeping memory bounded.
        self.assertGreaterEqual(MAX_TRANSCRIPT_CHARS, 1000)
        self.assertLessEqual(MAX_TRANSCRIPT_CHARS, 65536)


if __name__ == "__main__":
    unittest.main()
