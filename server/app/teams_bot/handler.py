"""Microsoft Teams bot handler for the hosted Wanderlux TripPlannerAgent.

Reuses the same ``attachment_store`` + ``attachment_extractor`` +
``MAFTravelOrchestrator`` used by the web UI; the only Teams-specific
work here is:

1. Turning ``turn_context.activity.conversation.id`` into the session id,
   so every Teams chat gets its own sticky attachment scope automatically.
2. Fetching attachment bytes via ``fetch_teams_attachment`` (paperclip
   uploads + pasted images; SharePoint links politely refused).
3. Force-routing every turn to ``TripPlannerAgent`` — this bot is the
   TripPlanner surface, not the full Wanderlux router.

The sticky-across-turns behaviour is implicit here: on every turn we call
``attachment_store.list_session(conversation.id)`` and pass all still-live
records to the orchestrator. TTL eviction (30 min) or the ``clear files``
command are the only ways attachments leave scope.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from botbuilder.core import ActivityHandler, TurnContext
from botbuilder.schema import Activity, ActivityTypes, ChannelAccount

from ..handler import attachment_extractor
from ..handler.attachment_extractor import (
    AttachmentExtractionError,
    AttachmentTooLargeError,
    UnsupportedAttachmentError,
)
from ..handler.attachment_store import AttachmentStore
from .attachment_fetch import fetch_teams_attachment

logger = logging.getLogger(__name__)


CLEAR_COMMANDS = {"clear", "reset", "clear files", "clear attachments", "/clear"}
TRIP_PLANNER_AGENT = "TripPlannerAgent"
USER_CONTEXT_AGENT = "UserContextAgent"


def _is_user_context_request(text: str) -> bool:
    normalized = text.lower()
    identity_terms = (
        "who am i",
        "who initiated",
        "signed-in user",
        "signed in user",
        "my profile",
        "microsoft graph",
        "graph knows",
        "job title",
        "department",
        "manager",
        "office location",
        "user id",
        "entra client id",
        "entra id",
        "agent identity",
    )
    return any(term in normalized for term in identity_terms)


def _safe_activity_attr(obj, *names: str) -> str:
    for name in names:
        value = getattr(obj, name, None)
        if value:
            return str(value)
    return ""


def _activity_identity_context(activity: Activity) -> dict:
    account = getattr(activity, "from_property", None) or getattr(activity, "from", None)
    return {
        "requester_identity": {
            "authenticated": True,
            "display_name": _safe_activity_attr(account, "name") or "Teams user",
            "email": _safe_activity_attr(account, "email", "user_principal_name", "userPrincipalName"),
            "identity_provider": "teams",
            "user_id": _safe_activity_attr(account, "aad_object_id", "aadObjectId", "id"),
        },
        "agent_identity": {
            "agent_name": TRIP_PLANNER_AGENT,
            "entra_client_id": os.environ.get(
                "TRIP_PLANNER_AGENT_ENTRA_CLIENT_ID",
                os.environ.get("AZURE_CLIENT_ID", os.environ.get("AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID", "")),
            ),
            "teams_bot_app_id": os.environ.get("MICROSOFT_APP_ID", ""),
        },
    }
DEFAULT_WELCOME_MESSAGE = (
    "Hi — I'm the Wanderlux Trip Planner. Tell me the destination, "
    "duration, and dates, and I'll draft a day-by-day itinerary. "
    "Attach booking PDFs or boarding passes and I'll plan around "
    "your real arrival times and hotel neighbourhood. Say "
    "`clear files` to drop everything I have in mind."
)


class TripPlannerBot(ActivityHandler):
    """Bot Framework handler that fronts a hosted Foundry specialist agent.

    Defaults to ``TripPlannerAgent`` for backwards compatibility, but the
    specialist can be overridden at construction time (wired to the
    ``TEAMS_BOT_SPECIALIST_AGENT`` env var by ``adapter_setup``) so the
    same bridge can front ``FlightBookingAgent``, ``OrchestratorAgent``,
    or any other prompt agent published to the Foundry project.
    """

    def __init__(
        self,
        *,
        orchestrator: Any,
        attachment_store: AttachmentStore,
        specialist_agent: str = TRIP_PLANNER_AGENT,
        welcome_message: str = DEFAULT_WELCOME_MESSAGE,
    ) -> None:
        self._orchestrator = orchestrator
        self._store = attachment_store
        self._specialist_agent = specialist_agent
        self._welcome_message = welcome_message

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_members_added_activity(
        self, members_added: list[ChannelAccount], turn_context: TurnContext
    ) -> None:
        recipient_id = turn_context.activity.recipient.id
        for member in members_added:
            if member.id == recipient_id:
                continue
            await turn_context.send_activity(self._welcome_message)

    # ------------------------------------------------------------------
    # Main turn handler
    # ------------------------------------------------------------------

    async def on_message_activity(self, turn_context: TurnContext) -> None:
        activity = turn_context.activity
        session_id = activity.conversation.id
        text = (activity.text or "").strip()

        newly_added = await self._ingest_attachments(activity, session_id, turn_context)

        if text.lower() in CLEAR_COMMANDS:
            count = self._store.clear_session(session_id)
            msg = (
                "All attached files cleared." if count
                else "There are no attached files to clear."
            )
            await turn_context.send_activity(msg)
            return

        if not text:
            if not newly_added:
                await turn_context.send_activity(
                    "Tell me the destination, duration, and dates and I'll plan the trip."
                )
            return

        # Re-hydrate every still-live attachment for this Teams conversation.
        # No client-side ``attachment_ids`` plumbing needed: the store IS the
        # source of truth per conversation.id, so sticky-across-turns is
        # automatic (mirroring the web UI's chip behaviour without any UI
        # state to keep in sync).
        active_records = self._store.list_session(session_id)
        attachments = [
            {
                "attachment_id": r.attachment_id,
                "filename": r.filename,
                "kind": r.kind,
                "text": r.extracted_text,
            }
            for r in active_records
        ]

        typing = Activity(type=ActivityTypes.typing)
        await turn_context.send_activity(typing)

        agent_name = (
            USER_CONTEXT_AGENT if _is_user_context_request(text) else self._specialist_agent
        )

        try:
            result = await self._orchestrator.run_specialist(
                agent_name,
                message=text,
                context={
                    "channel": "teams",
                    "conversation_id": session_id,
                    **_activity_identity_context(activity),
                },
                attachments=attachments or None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("teams_orchestrator_failed conversation=%s", session_id)
            await turn_context.send_activity(
                f"Sorry — I couldn't reach {agent_name} right now. ({exc})"
            )
            return

        # Teams renders Markdown natively (headings, bold, lists) which is
        # what the TripPlannerAgent already produces. Upgrade to an Adaptive
        # Card only when we need buttons / richer layout.
        await turn_context.send_activity(result.summary)

    # ------------------------------------------------------------------
    # Attachment ingest
    # ------------------------------------------------------------------

    async def _ingest_attachments(
        self, activity: Activity, session_id: str, turn_context: TurnContext
    ) -> list[str]:
        added: list[str] = []
        for att in activity.attachments or []:
            try:
                blob, filename, mime = await fetch_teams_attachment(att, turn_context)
            except Exception as exc:  # noqa: BLE001
                logger.warning("teams_attachment_fetch_failed error=%s", exc)
                await turn_context.send_activity(
                    f"Could not download **{_display_name(att)}** ({exc})."
                )
                continue

            if blob is None:
                await turn_context.send_activity(
                    f"Skipped **{_display_name(att)}** — please attach the "
                    "file directly to the chat rather than sharing a link."
                )
                continue

            try:
                extracted = await attachment_extractor.extract(
                    raw_bytes=blob, mime=mime, filename=filename,
                )
            except UnsupportedAttachmentError as exc:
                await turn_context.send_activity(f"I can't read **{filename}** — {exc}")
                continue
            except AttachmentTooLargeError as exc:
                await turn_context.send_activity(f"**{filename}** is too large — {exc}")
                continue
            except AttachmentExtractionError as exc:
                logger.warning(
                    "teams_attachment_extract_failed filename=%s error=%s",
                    filename, exc,
                )
                await turn_context.send_activity(
                    f"I could not read **{filename}** — please try a different file."
                )
                continue

            try:
                record = self._store.add(
                    session_id=session_id,
                    filename=filename,
                    kind=extracted.kind,
                    mime=extracted.mime,
                    size_bytes=len(blob),
                    extracted_text=extracted.extracted_text,
                )
            except ValueError as exc:
                await turn_context.send_activity(str(exc))
                continue

            added.append(filename)
            logger.info(
                "teams_attachment_added conversation=%s attachment_id=%s "
                "filename=%s size=%s",
                session_id, record.attachment_id, filename, len(blob),
            )

        if added:
            names = ", ".join(f"**{n}**" for n in added)
            await turn_context.send_activity(
                f"Added {names}. I'll keep it in mind on every turn until you "
                "say `clear files`."
            )
        return added


def _display_name(attachment: Any) -> str:
    return getattr(attachment, "name", None) or "attachment"
