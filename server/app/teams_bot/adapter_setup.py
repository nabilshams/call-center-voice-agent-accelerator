"""Cloud adapter + bot construction for the Teams bridge.

Kept in its own module so ``server.py`` can lazy-import it: importing
``botbuilder-core`` at server boot would otherwise fail whenever the
Teams bot is disabled (i.e. ``MICROSOFT_APP_ID`` unset).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class _BotSettings:
    """Attribute-shaped settings object for ``ConfigurationBotFrameworkAuthentication``.

    The Bot Framework SDK's ``ConfigurationServiceClientCredentialFactory``
    reads ``APP_ID``, ``APP_PASSWORD``, ``APP_TYPE`` and ``APP_TENANTID``
    via ``getattr``, so we surface exactly those names.
    """

    def __init__(self) -> None:
        self.APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
        self.APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")
        self.APP_TYPE = os.environ.get("MICROSOFT_APP_TYPE", "MultiTenant")
        self.APP_TENANTID = os.environ.get("MICROSOFT_APP_TENANT_ID", "")


def _resolve_appinsights_ikey() -> str:
    """Extract an App Insights instrumentation key from the standard env vars.

    ``botbuilder-applicationinsights`` predates connection strings and only
    accepts an ikey, so we parse it out of ``APPLICATIONINSIGHTS_CONNECTION_STRING``
    when the modern form is set (which is what the container app injects).
    Returns an empty string when App Insights is not configured.
    """
    ikey = os.environ.get("APPINSIGHTS_INSTRUMENTATIONKEY", "").strip()
    if ikey:
        return ikey
    conn = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip()
    if not conn:
        return ""
    for part in conn.split(";"):
        key, sep, value = part.partition("=")
        if sep and key.strip().lower() == "instrumentationkey":
            return value.strip()
    return ""


def _build_telemetry_middleware() -> Any:
    """Return a ``TelemetryLoggerMiddleware`` bound to App Insights, or None.

    Silently disabled when either the SDK package is missing or when the
    connection string is not set — the bot still works, just without the
    per-activity events flowing to App Insights.
    """
    try:
        from botbuilder.applicationinsights import ApplicationInsightsTelemetryClient
        from botbuilder.core import TelemetryLoggerMiddleware
    except ImportError:
        logger.info(
            "teams_bot_telemetry_disabled reason=botbuilder-applicationinsights_not_installed"
        )
        return None

    ikey = _resolve_appinsights_ikey()
    if not ikey:
        logger.info("teams_bot_telemetry_disabled reason=no_appinsights_connection_string")
        return None

    try:
        telemetry_client = ApplicationInsightsTelemetryClient(instrumentation_key=ikey)
        # log_personal_information=False redacts message text so we don't
        # spray customer PII into App Insights. Flip to True in a controlled
        # environment if you need message-body debugging.
        middleware = TelemetryLoggerMiddleware(
            telemetry_client, log_personal_information=False
        )
        logger.info("teams_bot_telemetry_enabled ikey=%s...", ikey[:8])
        return middleware
    except Exception as exc:  # noqa: BLE001
        logger.exception("teams_bot_telemetry_init_failed error=%s", exc)
        return None


def build_adapter_and_bot(*, orchestrator: Any, attachment_store: Any) -> tuple[Any, Any]:
    """Construct the ``CloudAdapter`` and ``TripPlannerBot``.

    Raises ``RuntimeError`` if ``MICROSOFT_APP_ID`` is not set. Callers
    are expected to gate the call on that env var and surface a 503 when
    the bot is disabled.
    """
    if not os.environ.get("MICROSOFT_APP_ID"):
        raise RuntimeError("MICROSOFT_APP_ID is not set; Teams bot is disabled.")

    # Lazy imports so the whole ``botbuilder`` stack is optional at server boot.
    # ``CloudAdapter`` + ``ConfigurationBotFrameworkAuthentication`` live in the
    # ``botbuilder-integration-aiohttp`` package, not ``botbuilder-core``. The
    # class itself is transport-agnostic -- Quart passes the raw activity /
    # auth header to ``adapter.process_activity`` directly.
    from botbuilder.integration.aiohttp import (
        CloudAdapter,
        ConfigurationBotFrameworkAuthentication,
    )

    from .handler import DEFAULT_WELCOME_MESSAGE, TRIP_PLANNER_AGENT, TripPlannerBot

    settings = _BotSettings()
    adapter = CloudAdapter(ConfigurationBotFrameworkAuthentication(settings))
    adapter.on_turn_error = _on_turn_error  # type: ignore[assignment]

    telemetry_middleware = _build_telemetry_middleware()
    if telemetry_middleware is not None:
        adapter.use(telemetry_middleware)

    # Which Foundry prompt agent the bot fronts. Defaults to TripPlannerAgent
    # to preserve backwards compatibility; set TEAMS_BOT_SPECIALIST_AGENT to
    # front a different agent (e.g. FlightBookingAgent, OrchestratorAgent).
    specialist_agent = os.environ.get(
        "TEAMS_BOT_SPECIALIST_AGENT", TRIP_PLANNER_AGENT
    ).strip() or TRIP_PLANNER_AGENT
    welcome_message = (
        os.environ.get("TEAMS_BOT_WELCOME_MESSAGE", "").strip()
        or DEFAULT_WELCOME_MESSAGE
    )

    bot = TripPlannerBot(
        orchestrator=orchestrator,
        attachment_store=attachment_store,
        specialist_agent=specialist_agent,
        welcome_message=welcome_message,
    )
    logger.info(
        "teams_bot_initialized app_id=%s... specialist_agent=%s",
        settings.APP_ID[:8], specialist_agent,
    )
    return adapter, bot


async def _on_turn_error(context: Any, error: Exception) -> None:
    """Global adapter error handler -- log and reply once instead of crashing."""
    logger.exception("teams_bot_turn_error error=%s", error)
    try:
        await context.send_activity(
            "Sorry — something went wrong on my end. Please try again in a moment."
        )
    except Exception:  # noqa: BLE001
        # If even the reply fails, swallow; the adapter middleware would
        # otherwise re-raise and disconnect the socket.
        pass
