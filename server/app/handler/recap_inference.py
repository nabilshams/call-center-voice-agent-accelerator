"""Recap-specific LLM inference: clinician/patient diarization + SOAP note.

Subclasses :class:`PersonaInferenceService` to:

* Bias diarization to a fixed two-role taxonomy: ``Clinician`` / ``Patient``
  (third-party speakers map to ``Other``).
* Replace the generic conversation summary with a structured SOAP-shaped
  clinical note (``chief_complaint``, ``subjective``, ``objective``,
  ``assessment``, ``plan``) with a per-section confidence chip.
* Expose :meth:`generate_section` so a single section can be regenerated on
  demand from an HTTP endpoint after the encounter has ended.

This service is read-only with respect to the transcript: it consumes
final segments via :meth:`add_segment` (inherited) and produces messages
through the same ``message_sender`` queue used by
``SpeechTranscriptionHandler``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from .persona_inference import PersonaInferenceService

logger = logging.getLogger(__name__)


SOAP_SECTIONS: tuple[str, ...] = (
    "chief_complaint",
    "subjective",
    "objective",
    "assessment",
    "plan",
)


SECTION_GUIDANCE: dict[str, str] = {
    "chief_complaint": (
        "One short sentence in the patient's own words (or paraphrased) stating "
        "the primary reason for the visit."
    ),
    "subjective": (
        "Narrative History of Present Illness in 2-5 short paragraphs. Include "
        "onset, duration, character, aggravating/alleviating factors, associated "
        "symptoms, and pertinent positives/negatives explicitly stated by the "
        "patient. Add a brief Review of Systems bullet list ONLY if the "
        "clinician asked or the patient volunteered system-by-system info."
    ),
    "objective": (
        "Bullet list of vital signs and physical exam findings stated by the "
        "clinician (e.g. 'Lungs: clear to auscultation bilaterally'). If none "
        "were stated, write 'Not documented in this encounter.' Only include "
        "exam findings that were explicitly mentioned."
    ),
    "assessment": (
        "Numbered list of working diagnoses or active problems, each followed by "
        "a one-sentence clinical reasoning that cites what was said in the "
        "encounter. If a differential was discussed, include it."
    ),
    "plan": (
        "Bulleted plan organised by problem when possible. Include medications "
        "(name, dose, frequency if stated), labs/imaging ordered, referrals, "
        "patient education given, and follow-up instructions. Mark each item "
        "with [Med], [Lab], [Imaging], [Referral], [Education], or [Follow-up]."
    ),
}


def _build_soap_system_msg() -> str:
    sections_block = "\n".join(
        f'    "{name}": {{"text": "<markdown>", "confidence": "low|medium|high"}},'
        for name in SOAP_SECTIONS
    )
    guidance_block = "\n".join(
        f"- {name}: {SECTION_GUIDANCE[name]}" for name in SOAP_SECTIONS
    )
    return (
        "You are an experienced medical scribe assisting a licensed clinician. "
        "Your task is to convert the transcript of a clinician-patient visit "
        "into a structured SOAP note that the clinician will review and edit "
        "before signing. Return JSON only, matching this schema exactly:\n"
        "{\n"
        '  "sections": {\n'
        f"{sections_block}\n"
        "  }\n"
        "}\n\n"
        "Section guidance:\n"
        f"{guidance_block}\n\n"
        "Style guidance:\n"
        "- Use neutral clinical language and stay grounded in what was said.\n"
        "- Prefer omitting a detail over guessing it; mark unclear sections "
        "with confidence 'low' and a short placeholder such as "
        "'*Not discussed in this encounter.*'.\n"
        "- Use compact Markdown (bullet lists, short paragraphs) inside the "
        "`text` field.\n"
        "- Set `confidence` to 'high' only when the section is well supported "
        "by explicit statements in the transcript.\n"
        "- Output JSON only, with no surrounding commentary or code fences."
    )


def _build_section_system_msg(section: str) -> str:
    guidance = SECTION_GUIDANCE.get(section, "")
    return (
        "You are an experienced medical scribe assisting a licensed clinician. "
        f"Generate only the `{section}` section of a SOAP note from the "
        "transcript provided. Return JSON only, matching this schema:\n"
        "{\n"
        '  "text": "<markdown>",\n'
        '  "confidence": "low|medium|high"\n'
        "}\n\n"
        f"Section guidance: {guidance}\n\n"
        "Stay grounded in what was said. If the section has no information, "
        "use a short placeholder and confidence 'low'."
    )


class RecapInferenceService(PersonaInferenceService):
    """LLM service tuned for clinical (clinician-patient) encounters."""

    def _build_diarization_system_msg(self) -> str:  # type: ignore[override]
        return (
            "You analyze a live speech-to-text transcript of a CLINICAL "
            "encounter between a clinician and a patient (occasionally a "
            "third party such as a family member or interpreter) and "
            "reconstruct who said what. Return STRICT JSON only matching "
            "exactly this schema:\n"
            "{\n"
            '  "speakers": {\n'
            '    "Speaker 1": {"persona": "Clinician|Patient|Other", '
            '"confidence": "low|medium|high", '
            '"rationale": "<one short sentence>"},\n'
            '    "Speaker 2": {...}\n'
            "  },\n"
            '  "turns": [\n'
            '    {"speaker": "Speaker 1", "text": "<verbatim utterance>"},\n'
            '    {"speaker": "Speaker 2", "text": "..."}\n'
            "  ]\n"
            "}\n\n"
            "RULES:\n"
            "- Persona MUST be exactly one of: 'Clinician', 'Patient', 'Other'. "
            "Use 'Other' for family members, interpreters, nurses, etc.\n"
            "- Use stable labels 'Speaker 1', 'Speaker 2'. The same role keeps "
            "the same label across calls.\n"
            "- Cover the ENTIRE transcript in `turns`, preserving wording.\n"
            "- Bias toward two speakers (Clinician + Patient) unless the "
            "transcript clearly shows a third voice.\n"
            "- The clinician usually drives the conversation with directed "
            "questions and gives instructions; the patient describes symptoms."
        )

    # Override `_run_once` only minimally: replace the system message used by
    # the base class. The base implementation builds the system message inline
    # so we monkey-patch by re-running the same logic. Cleanest approach is to
    # let the base class build the prompt but replace it before calling. We do
    # that by overriding the method entirely (small duplication, clearer).
    async def _run_once(self):  # type: ignore[override]
        self._ensure_client()
        if not self._client:
            return

        full_transcript = " ".join(self._segments).strip()
        snapshot_chars = self._segments_chars
        if not full_transcript:
            return

        system_msg = self._build_diarization_system_msg()
        user_msg = (
            "Live transcript so far (one continuous string from the speech "
            "recognizer, may contain merged turns and missing punctuation):\n\n"
            f"{full_transcript}\n\n"
            "Return STRICT JSON only."
        )

        try:
            resp = await self._client.chat.completions.create(
                model=self._deployment,
                temperature=0.0,
                max_tokens=1500,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("[RecapInference] Diarization LLM call failed: %s", e)
            return

        content = (resp.choices[0].message.content or "").strip()
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            logger.warning(
                "[RecapInference] Non-JSON diarization response: %s", content[:200]
            )
            return

        speakers_obj = parsed.get("speakers") or {}
        turns_obj = parsed.get("turns") or []
        if not isinstance(speakers_obj, dict) or not isinstance(turns_obj, list):
            return

        cleaned_personas: dict[str, dict] = {}
        for speaker, info in speakers_obj.items():
            if not isinstance(info, dict):
                continue
            persona = str(info.get("persona") or "Other").strip() or "Other"
            if persona not in {"Clinician", "Patient", "Other"}:
                # Force into the fixed taxonomy.
                persona = "Other"
            confidence = str(info.get("confidence") or "low").strip().lower()
            rationale = str(info.get("rationale") or "").strip()
            cleaned_personas[str(speaker)] = {
                "persona": persona,
                "confidence": confidence,
                "rationale": rationale,
            }

        cleaned_turns: list[dict] = []
        for turn in turns_obj:
            if not isinstance(turn, dict):
                continue
            spk = str(turn.get("speaker") or "").strip()
            text = str(turn.get("text") or "").strip()
            if not spk or not text:
                continue
            cleaned_turns.append({"speaker": spk, "text": text})

        if not cleaned_turns and not cleaned_personas:
            return

        self._last_personas = cleaned_personas
        self._last_turns = cleaned_turns
        self._last_inferred_chars = snapshot_chars

        payload: dict = {"type": "dialogue"}
        if cleaned_turns:
            payload["turns"] = cleaned_turns
        if cleaned_personas:
            payload["personas"] = cleaned_personas

        try:
            self._send(payload)
        except (RuntimeError, ValueError, TypeError) as e:
            logger.warning(
                "[RecapInference] Failed to enqueue dialogue update: %s", e
            )
        logger.info(
            "[RecapInference] Dialogue updated: %d turns, personas=%s",
            len(cleaned_turns), cleaned_personas,
        )

    # ------------------------------------------------------------------
    # SOAP note generation (replaces the generic summary).
    # ------------------------------------------------------------------

    def _format_transcript_for_note(self) -> str:
        if self._last_turns:
            return "\n".join(
                f"{self._role_for_speaker(t.get('speaker', ''))}: "
                f"{t.get('text', '')}"
                for t in self._last_turns
            )
        return " ".join(self._segments).strip()

    def _role_for_speaker(self, speaker: str) -> str:
        info = self._last_personas.get(speaker) or {}
        persona = info.get("persona") or speaker or "Speaker"
        return persona

    async def summarize(self) -> Optional[dict]:  # type: ignore[override]
        """Generate a structured SOAP note instead of a free-text summary."""
        if not self._enabled:
            return None
        # Wait briefly for any in-flight diarization to finish.
        for _ in range(20):
            if not self._inflight:
                break
            await asyncio.sleep(0.1)

        self._ensure_client()
        if not self._client:
            return None

        transcript_text = self._format_transcript_for_note()
        if not transcript_text:
            return None

        system_msg = _build_soap_system_msg()
        user_msg = (
            "Encounter transcript (clinician-patient visit; speaker labels "
            "are inferred from the audio):\n\n"
            f"{transcript_text}\n\n"
            "Return JSON only with the SOAP sections."
        )

        try:
            resp = await self._client.chat.completions.create(
                model=self._deployment,
                temperature=0.1,
                max_tokens=1800,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("[RecapInference] SOAP note LLM call failed: %s", e)
            return None

        content = (resp.choices[0].message.content or "").strip()
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            logger.warning(
                "[RecapInference] Non-JSON SOAP response: %s", content[:200]
            )
            return None

        sections_raw = parsed.get("sections") or {}
        if not isinstance(sections_raw, dict):
            return None

        sections: dict[str, dict] = {}
        for name in SOAP_SECTIONS:
            section_val = sections_raw.get(name) or {}
            if not isinstance(section_val, dict):
                continue
            text = str(section_val.get("text") or "").strip()
            confidence = str(section_val.get("confidence") or "low").strip().lower()
            if confidence not in {"low", "medium", "high"}:
                confidence = "low"
            sections[name] = {"text": text, "confidence": confidence}

        if not sections:
            return None

        return {
            "sections": sections,
            "personas": dict(self._last_personas),
        }

    async def generate_section(
        self, section: str, transcript_text: Optional[str] = None
    ) -> Optional[dict]:
        """Generate (or regenerate) a single SOAP section.

        :param section: One of :data:`SOAP_SECTIONS`.
        :param transcript_text: Optional override; if not provided the last
            diarized transcript captured during the encounter is used.
        """
        if section not in SOAP_SECTIONS:
            return None
        if not self._enabled:
            return None

        self._ensure_client()
        if not self._client:
            return None

        text_to_use = (transcript_text or self._format_transcript_for_note()).strip()
        if not text_to_use:
            return None

        system_msg = _build_section_system_msg(section)
        user_msg = (
            "Encounter transcript:\n\n"
            f"{text_to_use}\n\n"
            f"Return STRICT JSON for the `{section}` section only."
        )

        try:
            resp = await self._client.chat.completions.create(
                model=self._deployment,
                temperature=0.1,
                max_tokens=700,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(
                "[RecapInference] Section regen LLM call failed (%s): %s", section, e
            )
            return None

        content = (resp.choices[0].message.content or "").strip()
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            logger.warning(
                "[RecapInference] Non-JSON section response: %s", content[:200]
            )
            return None

        text = str(parsed.get("text") or "").strip()
        confidence = str(parsed.get("confidence") or "low").strip().lower()
        if confidence not in {"low", "medium", "high"}:
            confidence = "low"
        if not text:
            return None
        return {"section": section, "text": text, "confidence": confidence}
