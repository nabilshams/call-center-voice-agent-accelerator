"""Background service that uses Azure OpenAI to infer the persona/role of
each speaker AND split a live transcript into speaker turns.

In practice, the Azure Speech SDK's real-time diarization is unreliable
for single-channel browser audio: every utterance often comes back as
"Unknown" and the SDK coalesces multiple speakers into a single result.

To produce a useful diarized view we therefore use the LLM as our
diarizer: as the conversation grows we feed the chronological list of
final transcript segments and ask the model to (a) infer the personas of
the speakers in the conversation and (b) split the running transcript
into per-speaker turns.

The result is pushed back to the client as a single ``dialogue`` message
containing the latest ``turns`` array and ``personas`` map; the client
re-renders the transcript whenever this arrives.

Design notes:
- Inference runs in a background asyncio task; only one call is ever in
  flight per handler instance.
- Trigger debounce: at most every ``MIN_INFERENCE_INTERVAL_S`` seconds,
  and only when at least ``MIN_NEW_CHARS`` characters of new transcript
  have accumulated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable, Optional

from azure.identity import ManagedIdentityCredential, get_bearer_token_provider

logger = logging.getLogger(__name__)

# Tuning constants
MIN_INFERENCE_INTERVAL_S: float = 5.0
MIN_NEW_CHARS: int = 60
MAX_TRANSCRIPT_CHARS: int = 8000


class PersonaInferenceService:
    """LLM-driven dialogue diarizer + persona inferer for live transcripts."""

    def __init__(
        self,
        config: dict,
        message_sender: Callable[[dict], None],
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ):
        self._endpoint = (config.get("AZURE_OPENAI_ENDPOINT") or "").rstrip("/")
        self._deployment = (config.get("AZURE_OPENAI_CHAT_DEPLOYMENT") or "").strip()
        self._client_id = config.get("AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID", "")
        self._send = message_sender
        self._loop = loop

        # Chronological log of final transcript segments. Each entry is the
        # full final text emitted by the Speech SDK; speakers may be merged
        # or labeled "Unknown" - the LLM will reconstruct turns.
        self._segments: list[str] = []
        self._segments_chars: int = 0
        self._last_inferred_chars: int = 0
        self._last_personas: dict[str, dict] = {}
        self._last_turns: list[dict] = []
        self._last_run_at: float = 0.0
        self._inflight: bool = False
        self._lock = asyncio.Lock()

        self._client = None
        self._enabled = bool(self._endpoint and self._deployment)
        if not self._enabled:
            logger.info(
                "[PersonaInference] Disabled (endpoint set: %s, deployment set: %s)",
                bool(self._endpoint), bool(self._deployment),
            )

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def add_segment(self, _speaker: str, text: str):
        """Record a final transcript segment.

        The raw speaker label coming from the Speech SDK is ignored: the
        SDK's diarization is unreliable for browser mic audio and tends to
        collapse all voices into a single "Speaker 1". The LLM will
        re-diarize the chronological text.

        Safe to call from any thread (Speech SDK callbacks run off the
        asyncio loop).
        """
        if not self._enabled or not text:
            return
        cleaned = text.strip()
        if not cleaned:
            return

        self._segments.append(cleaned)
        self._segments_chars += len(cleaned) + 1
        # Bound memory: drop oldest entries while keeping the most recent
        # context. We keep enough to give the LLM stable continuity.
        while self._segments_chars > MAX_TRANSCRIPT_CHARS and len(self._segments) > 1:
            removed = self._segments.pop(0)
            self._segments_chars -= len(removed) + 1

        if not self._loop or not self._loop.is_running():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._maybe_run(), self._loop)
        except RuntimeError:
            # Loop is closing; ignore.
            pass

    async def _maybe_run(self):
        if self._inflight:
            return
        now = time.monotonic()
        if now - self._last_run_at < MIN_INFERENCE_INTERVAL_S:
            return
        if self._segments_chars - self._last_inferred_chars < MIN_NEW_CHARS:
            return

        async with self._lock:
            if self._inflight:
                return
            self._inflight = True
            self._last_run_at = time.monotonic()

        try:
            await self._run_once()
        finally:
            self._inflight = False

    def _ensure_client(self):
        if self._client is not None:
            return
        try:
            from openai import AsyncAzureOpenAI  # type: ignore
        except ImportError:
            logger.exception("[PersonaInference] openai package not available")
            self._enabled = False
            return

        if not self._client_id:
            logger.error("[PersonaInference] No managed identity client id configured")
            self._enabled = False
            return

        credential = ManagedIdentityCredential(client_id=self._client_id)
        token_provider = get_bearer_token_provider(
            credential, "https://cognitiveservices.azure.com/.default"
        )
        self._client = AsyncAzureOpenAI(
            azure_endpoint=self._endpoint,
            azure_ad_token_provider=token_provider,
            api_version="2024-10-21",
        )

    async def _run_once(self):
        self._ensure_client()
        if not self._client:
            return

        # Snapshot the running transcript.
        full_transcript = " ".join(self._segments).strip()
        snapshot_chars = self._segments_chars
        if not full_transcript:
            return

        system_msg = (
            "You analyze a live speech-to-text transcript of a conversation "
            "between two or more people and reconstruct who said what. "
            "You always return STRICT JSON only, no prose, matching exactly "
            "this schema:\n"
            "{\n"
            '  "speakers": {\n'
            '    "Speaker 1": {"persona": "<short role e.g. Doctor>", '
            '"confidence": "low|medium|high", '
            '"rationale": "<one short sentence>"},\n'
            '    "Speaker 2": {...}\n'
            "  },\n"
            '  "turns": [\n'
            '    {"speaker": "Speaker 1", "text": "<verbatim utterance>"},\n'
            '    {"speaker": "Speaker 2", "text": "..."}\n'
            "  ]\n"
            "}\n\n"
            "Rules:\n"
            "- Use labels 'Speaker 1', 'Speaker 2', etc. Keep labels stable "
            "across calls (the same role should keep the same label).\n"
            "- Cover the ENTIRE transcript in `turns`, preserving original "
            "wording as closely as possible. Split where it is clear that the "
            "speaker changes (questions vs answers, greetings, role hints).\n"
            "- If you cannot determine roles, use 'Unknown' for persona and "
            "confidence 'low'.\n"
            "- Use only personas that exist in the conversation; do not invent "
            "extra speakers."
        )

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
            logger.warning("[PersonaInference] LLM call failed: %s", e)
            return

        content = (resp.choices[0].message.content or "").strip()
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("[PersonaInference] Non-JSON LLM response: %s", content[:200])
            return

        speakers_obj = parsed.get("speakers") or {}
        turns_obj = parsed.get("turns") or []
        if not isinstance(speakers_obj, dict) or not isinstance(turns_obj, list):
            return

        cleaned_personas: dict[str, dict] = {}
        for speaker, info in speakers_obj.items():
            if not isinstance(info, dict):
                continue
            persona = str(info.get("persona") or "Unknown").strip() or "Unknown"
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
            logger.warning("[PersonaInference] Failed to enqueue dialogue update: %s", e)
        logger.info(
            "[PersonaInference] Dialogue updated: %d turns, personas=%s",
            len(cleaned_turns), cleaned_personas,
        )

    async def aclose(self):
        if self._client is not None:
            try:
                await self._client.close()
            except (RuntimeError, OSError):
                pass
            self._client = None

    async def summarize(self) -> Optional[dict]:
        """Generate a final summary of the conversation.

        Returns a dict with ``summary``, ``key_points`` (list of str),
        ``action_items`` (list of str), and ``personas`` (last known map),
        or ``None`` if there is nothing to summarize / the LLM is unavailable.
        """
        if not self._enabled:
            return None
        # If a dialogue inference is in progress, wait briefly for it to finish
        # so we can use its (better diarized) turns.
        for _ in range(20):
            if not self._inflight:
                break
            await asyncio.sleep(0.1)

        self._ensure_client()
        if not self._client:
            return None

        if self._last_turns:
            transcript_text = "\n".join(
                f"{t.get('speaker', 'Unknown')}: {t.get('text', '')}"
                for t in self._last_turns
            )
        else:
            transcript_text = " ".join(self._segments).strip()

        if not transcript_text:
            return None

        personas_block = (
            json.dumps(self._last_personas, ensure_ascii=False)
            if self._last_personas else "{}"
        )

        system_msg = (
            "You summarize a finished conversation transcript. Return STRICT "
            "JSON only matching exactly this schema:\n"
            "{\n"
            '  "summary": "<2-4 sentence neutral summary>",\n'
            '  "key_points": ["<short bullet>", ...],\n'
            '  "action_items": ["<short bullet, may be empty>", ...]\n'
            "}\n"
            "Be concise and factual. Do NOT invent facts that are not in the "
            "transcript."
        )
        user_msg = (
            f"Inferred speaker personas (JSON): {personas_block}\n\n"
            "Transcript:\n"
            f"{transcript_text}\n\n"
            "Return STRICT JSON only."
        )

        try:
            resp = await self._client.chat.completions.create(
                model=self._deployment,
                temperature=0.1,
                max_tokens=600,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("[PersonaInference] Summary LLM call failed: %s", e)
            return None

        content = (resp.choices[0].message.content or "").strip()
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("[PersonaInference] Non-JSON summary response: %s", content[:200])
            return None

        summary_text = str(parsed.get("summary") or "").strip()
        key_points_raw = parsed.get("key_points") or []
        action_items_raw = parsed.get("action_items") or []
        key_points = [str(x).strip() for x in key_points_raw if str(x).strip()] if isinstance(key_points_raw, list) else []
        action_items = [str(x).strip() for x in action_items_raw if str(x).strip()] if isinstance(action_items_raw, list) else []

        if not summary_text and not key_points and not action_items:
            return None

        return {
            "summary": summary_text,
            "key_points": key_points,
            "action_items": action_items,
            "personas": dict(self._last_personas),
        }
