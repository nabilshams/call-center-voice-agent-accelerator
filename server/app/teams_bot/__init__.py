"""Microsoft Teams bot bridge for the hosted TripPlannerAgent.

Only the bytes-acquisition path is Teams-specific; extraction, storage,
sticky-attachment behaviour, and hosted-agent invocation are all shared
with the web UI via ``attachment_extractor``, ``attachment_store``, and
``local_maf_orchestrator``.
"""

from .handler import TripPlannerBot
from .adapter_setup import build_adapter_and_bot

__all__ = ["TripPlannerBot", "build_adapter_and_bot"]
