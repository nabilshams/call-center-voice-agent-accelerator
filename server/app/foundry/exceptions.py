"""Errors raised by the reusable Foundry building blocks."""

from __future__ import annotations


class FoundryAgentManagementError(RuntimeError):
    """Raised when a Foundry agent management operation fails.

    Single, consistent failure type so callers can catch one exception rather
    than sprinkling azure-core try/except throughout their code.
    """
