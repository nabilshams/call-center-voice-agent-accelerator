"""Bytes acquisition for Microsoft Teams file attachments.

Bot Framework surfaces three flavours of file to a bot; we handle the two
inline cases and defer the SharePoint / OneDrive share case to v2 (it
needs Microsoft Graph delegated auth to resolve).

Case A -- ``application/vnd.microsoft.teams.file.download.info``
    User dropped a file into the compose box. ``content.downloadUrl`` is a
    pre-signed HTTPS URL; plain GET, no auth required.

Case B -- ``image/*``
    Pasted or dragged image. ``contentUrl`` is Bot Framework hosted and
    requires a bearer token derived from ``MICROSOFT_APP_ID`` /
    ``MICROSOFT_APP_PASSWORD``.

Case C -- link / reference (SharePoint / OneDrive)
    Requires Graph on-behalf-of. Skipped in v1; caller is told to attach
    the file directly.
"""

from __future__ import annotations

import logging
import mimetypes
import os
from typing import Any

import aiohttp
from botbuilder.core import TurnContext
from botbuilder.schema import Attachment

logger = logging.getLogger(__name__)


FILE_DOWNLOAD_INFO = "application/vnd.microsoft.teams.file.download.info"

_MIME_BY_EXT = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "tiff": "image/tiff",
    "tif": "image/tiff",
    "bmp": "image/bmp",
    "heif": "image/heif",
    "json": "application/json",
}


async def fetch_teams_attachment(
    attachment: Attachment, turn_context: TurnContext
) -> tuple[bytes | None, str, str]:
    """Return ``(bytes, filename, mime)`` for a Teams attachment.

    Returns ``(None, filename, content_type)`` for cases we deliberately do
    not support in v1 (e.g. SharePoint links) so the caller can surface a
    friendly "please attach the file directly" message instead of raising.
    """
    del turn_context  # bot credentials are read from env for Case B
    ctype = (attachment.content_type or "").lower()
    filename = attachment.name or "attachment"

    if ctype == FILE_DOWNLOAD_INFO:
        return await _fetch_direct_upload(attachment, filename)

    if ctype.startswith("image/"):
        return await _fetch_bot_hosted_image(attachment, filename, ctype)

    logger.info("teams_attachment_link_skipped content_type=%s", ctype)
    return (None, filename, ctype)


async def _fetch_direct_upload(
    attachment: Attachment, filename: str
) -> tuple[bytes | None, str, str]:
    content: dict[str, Any] = attachment.content or {}
    download_url = content.get("downloadUrl") or content.get("download_url")
    if not download_url:
        return (None, filename, "")

    file_type = str(content.get("fileType") or content.get("file_type") or "").lower()
    mime = (
        _MIME_BY_EXT.get(file_type)
        or mimetypes.guess_type(filename)[0]
        or "application/octet-stream"
    )
    async with aiohttp.ClientSession() as session:
        async with session.get(download_url) as resp:
            resp.raise_for_status()
            data = await resp.read()
    return (data, filename, mime)


async def _fetch_bot_hosted_image(
    attachment: Attachment, filename: str, ctype: str
) -> tuple[bytes | None, str, str]:
    url = attachment.content_url
    if not url:
        return (None, filename, ctype)

    headers: dict[str, str] = {}
    app_id = os.environ.get("MICROSOFT_APP_ID", "")
    app_password = os.environ.get("MICROSOFT_APP_PASSWORD", "")
    if app_id and app_password:
        try:
            # Local import: keeps botbuilder optional at module import time so
            # the rest of the server still boots when the Teams bot is off.
            from botframework.connector.auth import MicrosoftAppCredentials

            creds = MicrosoftAppCredentials(app_id, app_password)
            token = creds.get_access_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("bot_credential_token_failed error=%s", exc)

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            data = await resp.read()
    return (data, filename, ctype)
