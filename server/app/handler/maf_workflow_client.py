"""Client for Microsoft Agent Framework orchestration workflow endpoints."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from azure.core.exceptions import AzureError
from azure.identity.aio import DefaultAzureCredential, ManagedIdentityCredential

logger = logging.getLogger(__name__)


class MAFWorkflowError(RuntimeError):
    """Raised when Microsoft Agent Framework workflow invocation fails."""


class MAFWorkflowClient:
    """Invokes a Microsoft Agent Framework workflow endpoint and normalizes payloads."""

    def __init__(self, config: dict[str, Any]):
        self.endpoint = (config.get("MAF_WORKFLOW_ENDPOINT") or "").rstrip("/")
        self.workflow_path = config.get("MAF_WORKFLOW_PATH", "")
        self.api_key = config.get("MAF_API_KEY", "")
        self.client_id = config.get("AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID", "")
        self.timeout_seconds = int(config.get("MAF_WORKFLOW_TIMEOUT_SECONDS", 25))

    def is_configured(self) -> bool:
        return bool(self.endpoint and self.workflow_path)

    async def invoke(self, message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.is_configured():
            raise MAFWorkflowError("Microsoft Agent Framework workflow is not configured")

        headers = await self._build_headers()
        payload = {
            "message": message,
            "context": context or {},
        }

        url = f"{self.endpoint}/{self.workflow_path.lstrip('/')}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
                ) as response:
                    if response.status >= 400:
                        text = await response.text()
                        raise MAFWorkflowError(
                            f"Microsoft Agent Framework workflow call failed: HTTP {response.status}: {text}"
                        )
                    data = await response.json()
                    return self._normalize(data)
        except asyncio.TimeoutError as exc:
            raise MAFWorkflowError("Microsoft Agent Framework workflow call timed out") from exc

    async def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}

        if self.api_key:
            headers["api-key"] = self.api_key
            return headers

        token = None
        if self.client_id:
            try:
                async with ManagedIdentityCredential(client_id=self.client_id) as credential:
                    token = await credential.get_token("https://cognitiveservices.azure.com/.default")
            except (AzureError, RuntimeError, ValueError) as exc:
                logger.warning(
                    "Managed identity auth failed for Microsoft Agent Framework workflow: %s",
                    exc,
                )

        if token is None:
            async with DefaultAzureCredential() as credential:
                token = await credential.get_token("https://cognitiveservices.azure.com/.default")

        headers["Authorization"] = f"Bearer {token.token}"
        return headers

    @staticmethod
    def _normalize(data: dict[str, Any]) -> dict[str, Any]:
        # Keep compatibility with the shared app orchestration contract.
        return {
            "spoken_reply": data.get("spoken_reply") or data.get("reply") or "I can help with that.",
            "clarification_question": data.get("clarification_question"),
            "selected_agents": data.get("selected_agents", []),
            "specialist_outputs": data.get("specialist_outputs", []),
            "confidence": data.get("confidence", 0.0),
            "next_step": data.get("next_step", "present_options"),
            "raw": data,
        }
