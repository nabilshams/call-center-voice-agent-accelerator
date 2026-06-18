"""Persistence for the travel agency demo script.

The codebase ships a seed script (``app/data/travel_agency_demo_script.json``).
When a user edits and saves the script from the UI, the changes are written to a
runtime copy so the original seed remains intact. Loading prefers the runtime
copy and falls back to the seed.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "travel_agency_demo_script.json"


class DemoScriptStorage:
    """File-based storage for the editable demo script."""

    def __init__(self, runtime_path: str | None = None):
        self._seed_path = _SEED_PATH
        default_runtime = self._seed_path.parent / "travel_agency_demo_script.runtime.json"
        self._runtime_path = Path(runtime_path) if runtime_path else default_runtime

    def load(self) -> dict[str, Any]:
        """Return the saved runtime script, or the shipped seed as a fallback."""
        for path in (self._runtime_path, self._seed_path):
            try:
                if path.exists():
                    with path.open("r", encoding="utf-8") as handle:
                        data = json.load(handle)
                    if isinstance(data, dict) and isinstance(data.get("sections"), list):
                        return self._normalize(data)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Failed to read demo script from %s: %s", path, exc)
        return {"sections": []}

    def save(self, data: dict[str, Any]) -> dict[str, Any]:
        """Persist the script to the runtime copy and return the normalized form."""
        normalized = self._normalize(data)
        self._runtime_path.parent.mkdir(parents=True, exist_ok=True)
        with self._runtime_path.open("w", encoding="utf-8") as handle:
            json.dump(normalized, handle, indent=2, ensure_ascii=False)
        return normalized

    def reset(self) -> dict[str, Any]:
        """Drop the runtime override so the codebase seed is used again."""
        try:
            if self._runtime_path.exists():
                self._runtime_path.unlink()
        except OSError as exc:
            logger.warning("Failed to reset demo script: %s", exc)
        return self.load()

    @staticmethod
    def _normalize(data: dict[str, Any]) -> dict[str, Any]:
        """Coerce arbitrary input into the canonical demo-script shape."""
        sections: list[dict[str, Any]] = []
        raw_sections = data.get("sections") if isinstance(data, dict) else None
        if isinstance(raw_sections, list):
            for raw in raw_sections:
                if not isinstance(raw, dict):
                    continue
                title = str(raw.get("title", "")).strip()
                note = str(raw.get("note", "")).strip()
                lines = [
                    str(line).strip()
                    for line in raw.get("lines", [])
                    if isinstance(line, (str, int, float)) and str(line).strip()
                ]
                if title or note or lines:
                    sections.append({"title": title, "note": note, "lines": lines})
        return {"sections": sections}
