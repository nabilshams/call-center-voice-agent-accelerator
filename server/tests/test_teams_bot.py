"""Unit tests for the Microsoft Teams bot bridge (``app.teams_bot``).

Covers:

  * welcome message on ``on_members_added_activity`` (excluding self)
  * ``clear files`` command clears the store and does NOT hit the orchestrator
  * every message re-hydrates all live attachments for the conversation
    (sticky-across-turns behaviour, no client-side plumbing needed)
  * attachment ingest: file-download-info case is fetched, extracted, and
    stored; SharePoint-link case is politely refused
  * attachment extraction failures reply with a user-facing error and skip
    the store

The Bot Framework SDK is stubbed with lightweight namespaces so no real
Bot Service or Teams call is made.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.handler.attachment_extractor import (
    AttachmentExtractionError,
    ExtractedAttachment,
    UnsupportedAttachmentError,
)
from app.handler.attachment_store import AttachmentStore
from app.teams_bot import attachment_fetch as fetch_mod
from app.teams_bot.handler import CLEAR_COMMANDS, TRIP_PLANNER_AGENT, TripPlannerBot


# ---------------------------------------------------------------------------
# Minimal fakes for Bot Framework types we depend on
# ---------------------------------------------------------------------------


class _FakeTurnContext:
    """Just enough of ``botbuilder.core.TurnContext`` for our handler."""

    def __init__(self, activity):
        self.activity = activity
        self.sent: list = []

    async def send_activity(self, message):
        self.sent.append(message)

    @property
    def sent_texts(self) -> list[str]:
        out = []
        for m in self.sent:
            text = getattr(m, "text", None)
            out.append(text if text is not None else str(m))
        return out


def _msg(
    text: str = "hello",
    conversation_id: str = "conv-123",
    attachments=None,
    from_account=None,
) -> SimpleNamespace:
    return SimpleNamespace(
        type="message",
        text=text,
        conversation=SimpleNamespace(id=conversation_id),
        from_property=from_account,
        recipient=SimpleNamespace(id="bot"),
        attachments=attachments or [],
    )


class _RecordingOrchestrator:
    def __init__(self, summary: str = "Here is your plan."):
        self.summary = summary
        self.calls: list[dict] = []

    async def run_specialist(self, agent_name, *, message, context, attachments=None):
        self.calls.append({
            "agent": agent_name,
            "message": message,
            "context": context,
            "attachments": attachments,
        })
        return SimpleNamespace(agent=agent_name, summary=self.summary, confidence=0.9)


# ---------------------------------------------------------------------------
# Message / lifecycle tests
# ---------------------------------------------------------------------------


class WelcomeMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_greets_added_user_but_not_self(self):
        orch = _RecordingOrchestrator()
        store = AttachmentStore()
        bot = TripPlannerBot(orchestrator=orch, attachment_store=store)

        activity = SimpleNamespace(
            type="conversationUpdate",
            recipient=SimpleNamespace(id="bot"),
        )
        ctx = _FakeTurnContext(activity)
        added = [SimpleNamespace(id="user-1"), SimpleNamespace(id="bot")]

        await bot.on_members_added_activity(added, ctx)

        # One welcome (for user-1); the bot itself is filtered out.
        self.assertEqual(len(ctx.sent), 1)
        self.assertIn("Wanderlux Trip Planner", ctx.sent_texts[0])


class ClearCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_clear_files_command_clears_store_and_skips_orchestrator(self):
        orch = _RecordingOrchestrator()
        store = AttachmentStore()
        store.add(
            session_id="conv-123",
            filename="boarding.pdf",
            kind="pdf",
            mime="application/pdf",
            size_bytes=1,
            extracted_text="x",
        )
        bot = TripPlannerBot(orchestrator=orch, attachment_store=store)

        ctx = _FakeTurnContext(_msg(text="clear files"))
        await bot.on_message_activity(ctx)

        self.assertEqual(store.list_session("conv-123"), [])
        self.assertEqual(orch.calls, [])  # No agent invocation!
        self.assertTrue(any("cleared" in t.lower() for t in ctx.sent_texts))

    async def test_all_clear_aliases_recognised(self):
        # Sanity: at least the canonical variants live in CLEAR_COMMANDS.
        for alias in {"clear", "reset", "clear files"}:
            self.assertIn(alias, CLEAR_COMMANDS)

    async def test_clear_on_empty_store_is_friendly(self):
        orch = _RecordingOrchestrator()
        store = AttachmentStore()
        bot = TripPlannerBot(orchestrator=orch, attachment_store=store)

        ctx = _FakeTurnContext(_msg(text="clear"))
        await bot.on_message_activity(ctx)

        self.assertTrue(any("no attached files" in t.lower() for t in ctx.sent_texts))


class OrchestratorRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_always_routes_to_trip_planner(self):
        orch = _RecordingOrchestrator()
        store = AttachmentStore()
        bot = TripPlannerBot(orchestrator=orch, attachment_store=store)

        ctx = _FakeTurnContext(_msg(text="Plan a trip to Lisbon."))
        await bot.on_message_activity(ctx)

        self.assertEqual(len(orch.calls), 1)
        call = orch.calls[0]
        self.assertEqual(call["agent"], TRIP_PLANNER_AGENT)
        self.assertEqual(call["message"], "Plan a trip to Lisbon.")
        self.assertEqual(call["context"]["channel"], "teams")
        self.assertEqual(call["context"]["conversation_id"], "conv-123")

    async def test_agent_summary_is_sent_as_reply(self):
        orch = _RecordingOrchestrator(summary="Day 1: arrive.")
        store = AttachmentStore()
        bot = TripPlannerBot(orchestrator=orch, attachment_store=store)

        ctx = _FakeTurnContext(_msg(text="Plan a trip."))
        await bot.on_message_activity(ctx)

        # We expect a typing indicator first, then the reply.
        replied = [t for t in ctx.sent_texts if "Day 1" in t]
        self.assertEqual(replied, ["Day 1: arrive."])

    async def test_sender_identity_is_forwarded_to_orchestrator(self):
        orch = _RecordingOrchestrator()
        store = AttachmentStore()
        bot = TripPlannerBot(orchestrator=orch, attachment_store=store)
        sender = SimpleNamespace(
            name="Ada Lovelace",
            email="ada@example.com",
            aad_object_id="aad-user-123",
            id="teams-user-123",
        )

        ctx = _FakeTurnContext(_msg(text="Who initiated this request?", from_account=sender))
        await bot.on_message_activity(ctx)

        identity = orch.calls[0]["context"]["requester_identity"]
        self.assertEqual(identity["display_name"], "Ada Lovelace")
        self.assertEqual(identity["email"], "ada@example.com")
        self.assertEqual(identity["user_id"], "aad-user-123")
        self.assertEqual(identity["identity_provider"], "teams")
        self.assertEqual(orch.calls[0]["context"]["agent_identity"]["agent_name"], TRIP_PLANNER_AGENT)

    async def test_empty_message_without_attachments_nudges_user(self):
        orch = _RecordingOrchestrator()
        store = AttachmentStore()
        bot = TripPlannerBot(orchestrator=orch, attachment_store=store)

        ctx = _FakeTurnContext(_msg(text=""))
        await bot.on_message_activity(ctx)

        self.assertEqual(orch.calls, [])
        self.assertTrue(any("destination" in t.lower() for t in ctx.sent_texts))

    async def test_orchestrator_error_is_sent_as_friendly_reply(self):
        class _BrokenOrchestrator(_RecordingOrchestrator):
            async def run_specialist(self, *args, **kwargs):
                raise RuntimeError("Foundry unreachable")

        store = AttachmentStore()
        bot = TripPlannerBot(orchestrator=_BrokenOrchestrator(), attachment_store=store)

        ctx = _FakeTurnContext(_msg(text="Plan a trip."))
        await bot.on_message_activity(ctx)

        self.assertTrue(any(
            "couldn't reach" in t.lower() and TRIP_PLANNER_AGENT.lower() in t.lower()
            for t in ctx.sent_texts
        ))


class StickyAttachmentTests(unittest.IsolatedAsyncioTestCase):
    """The Teams bot re-hydrates ALL live attachments for the conversation
    on every turn -- there is no client-side attachment_ids plumbing."""

    async def test_previously_added_attachment_is_re_sent_on_next_turn(self):
        orch = _RecordingOrchestrator()
        store = AttachmentStore()
        # Simulate a file added on a previous turn.
        record = store.add(
            session_id="conv-123",
            filename="boarding.pdf",
            kind="pdf",
            mime="application/pdf",
            size_bytes=42,
            extracted_text="TAP452 arrives 08:15",
        )
        bot = TripPlannerBot(orchestrator=orch, attachment_store=store)

        ctx = _FakeTurnContext(_msg(text="Any better hotels on the same street?"))
        await bot.on_message_activity(ctx)

        self.assertEqual(len(orch.calls), 1)
        attachments = orch.calls[0]["attachments"]
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["attachment_id"], record.attachment_id)
        self.assertEqual(attachments[0]["filename"], "boarding.pdf")
        self.assertEqual(attachments[0]["text"], "TAP452 arrives 08:15")

    async def test_no_attachments_call_passes_none(self):
        orch = _RecordingOrchestrator()
        store = AttachmentStore()
        bot = TripPlannerBot(orchestrator=orch, attachment_store=store)

        ctx = _FakeTurnContext(_msg(text="Plan a trip."))
        await bot.on_message_activity(ctx)

        self.assertIsNone(orch.calls[0]["attachments"])


class AttachmentIngestTests(unittest.IsolatedAsyncioTestCase):
    """Simulates a file arriving on the activity: we mock ``fetch_teams_attachment``
    and ``attachment_extractor.extract`` and assert the store + reply."""

    async def test_direct_upload_flows_through_store(self):
        orch = _RecordingOrchestrator()
        store = AttachmentStore()
        bot = TripPlannerBot(orchestrator=orch, attachment_store=store)

        activity_attachment = SimpleNamespace(
            content_type=fetch_mod.FILE_DOWNLOAD_INFO,
            name="hotel.pdf",
            content={"downloadUrl": "https://example/hotel.pdf", "fileType": "pdf"},
            content_url=None,
        )

        async def _fake_fetch(att, ctx):
            return (b"%PDF-1.4 fake", "hotel.pdf", "application/pdf")

        async def _fake_extract(*, raw_bytes, mime, filename):
            return ExtractedAttachment(
                kind="pdf", mime="application/pdf",
                extracted_text="Hotel Wanderlux, Barcelona",
            )

        with patch("app.teams_bot.handler.fetch_teams_attachment", side_effect=_fake_fetch), \
             patch("app.teams_bot.handler.attachment_extractor.extract", side_effect=_fake_extract):
            ctx = _FakeTurnContext(_msg(text="Plan around this.", attachments=[activity_attachment]))
            await bot.on_message_activity(ctx)

        records = store.list_session("conv-123")
        self.assertEqual([r.filename for r in records], ["hotel.pdf"])
        self.assertEqual(records[0].extracted_text, "Hotel Wanderlux, Barcelona")
        # The orchestrator sees the newly ingested file.
        self.assertEqual(orch.calls[0]["attachments"][0]["filename"], "hotel.pdf")
        # And the user gets an acknowledgement mentioning the filename.
        self.assertTrue(any("hotel.pdf" in t for t in ctx.sent_texts))

    async def test_sharepoint_link_is_politely_refused(self):
        orch = _RecordingOrchestrator()
        store = AttachmentStore()
        bot = TripPlannerBot(orchestrator=orch, attachment_store=store)

        link_attachment = SimpleNamespace(
            content_type="application/vnd.microsoft.card.hero",
            name="SharedDoc.pdf",
            content={},
            content_url="https://sharepoint.example/link",
        )

        async def _fake_fetch(att, ctx):
            # Simulate the Case C refusal path from attachment_fetch.py
            return (None, "SharedDoc.pdf", "application/vnd.microsoft.card.hero")

        with patch("app.teams_bot.handler.fetch_teams_attachment", side_effect=_fake_fetch):
            ctx = _FakeTurnContext(_msg(text="here", attachments=[link_attachment]))
            await bot.on_message_activity(ctx)

        self.assertEqual(store.list_session("conv-123"), [])
        self.assertTrue(any("attach the file directly" in t for t in ctx.sent_texts))

    async def test_extractor_error_reports_but_does_not_crash(self):
        orch = _RecordingOrchestrator()
        store = AttachmentStore()
        bot = TripPlannerBot(orchestrator=orch, attachment_store=store)

        activity_attachment = SimpleNamespace(
            content_type=fetch_mod.FILE_DOWNLOAD_INFO,
            name="corrupt.pdf",
            content={"downloadUrl": "https://example/corrupt.pdf", "fileType": "pdf"},
            content_url=None,
        )

        async def _fake_fetch(att, ctx):
            return (b"garbage", "corrupt.pdf", "application/pdf")

        with patch("app.teams_bot.handler.fetch_teams_attachment", side_effect=_fake_fetch), \
             patch(
                 "app.teams_bot.handler.attachment_extractor.extract",
                 side_effect=AttachmentExtractionError("could not read"),
             ):
            ctx = _FakeTurnContext(_msg(text="plan", attachments=[activity_attachment]))
            await bot.on_message_activity(ctx)

        self.assertEqual(store.list_session("conv-123"), [])
        self.assertTrue(any("corrupt.pdf" in t for t in ctx.sent_texts))

    async def test_unsupported_mime_reports_but_does_not_crash(self):
        orch = _RecordingOrchestrator()
        store = AttachmentStore()
        bot = TripPlannerBot(orchestrator=orch, attachment_store=store)

        activity_attachment = SimpleNamespace(
            content_type=fetch_mod.FILE_DOWNLOAD_INFO,
            name="mystery.exe",
            content={"downloadUrl": "https://example/x", "fileType": "exe"},
            content_url=None,
        )

        async def _fake_fetch(att, ctx):
            return (b"MZ...", "mystery.exe", "application/x-msdownload")

        with patch("app.teams_bot.handler.fetch_teams_attachment", side_effect=_fake_fetch), \
             patch(
                 "app.teams_bot.handler.attachment_extractor.extract",
                 side_effect=UnsupportedAttachmentError("nope"),
             ):
            ctx = _FakeTurnContext(_msg(text="here", attachments=[activity_attachment]))
            await bot.on_message_activity(ctx)

        self.assertEqual(store.list_session("conv-123"), [])
        self.assertTrue(any("can't read" in t.lower() for t in ctx.sent_texts))


if __name__ == "__main__":
    unittest.main()
