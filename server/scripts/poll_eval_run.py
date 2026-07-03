"""Poll a Foundry eval run to terminal state, then write a sentinel file.

Called as an async background job so the agent gets notified when the
evaluation reaches `completed` / `failed` / `cancelled`. Uses Azure CLI
credentials (matches the interactive az login).

Usage:
    python scripts/poll_eval_run.py <projectEndpoint> <evalId> <evalRunId> <sentinelPath>
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests
from azure.identity import AzureCliCredential

TERMINAL_STATES = {"Completed", "Failed", "Cancelled", "completed", "failed", "cancelled"}
POLL_SECONDS = 20
MAX_POLLS = 90  # cap at ~30 minutes


def main(project_endpoint: str, eval_id: str, eval_run_id: str, sentinel: str) -> int:
    sentinel_path = Path(sentinel)
    sentinel_path.parent.mkdir(parents=True, exist_ok=True)

    credential = AzureCliCredential(process_timeout=60)
    token = credential.get_token("https://ai.azure.com/.default").token
    headers = {"Authorization": f"Bearer {token}"}

    # Foundry data-plane API for a single eval run.
    # Format: {projectEndpoint}/evaluations/runs/{run_id}?api-version=...
    base = project_endpoint.rstrip("/")
    url = f"{base}/evaluations/runs/{eval_run_id}?api-version=2025-05-15-preview"

    last_status = None
    for i in range(MAX_POLLS):
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 401:
                # token may have expired for a long-running poll — refresh once
                token = credential.get_token("https://ai.azure.com/.default").token
                headers["Authorization"] = f"Bearer {token}"
                resp = requests.get(url, headers=headers, timeout=30)

            resp.raise_for_status()
            data = resp.json()
            status = data.get("status") or data.get("Status")

            if status != last_status:
                print(f"[poll {i+1}/{MAX_POLLS}] status={status}", flush=True)
                last_status = status

            if status in TERMINAL_STATES:
                sentinel_path.write_text(json.dumps(data, indent=2))
                print(f"terminal: {status} -> wrote {sentinel_path}", flush=True)
                return 0
        except Exception as exc:  # noqa: BLE001
            print(f"[poll {i+1}] error: {exc}", flush=True)

        time.sleep(POLL_SECONDS)

    print(f"poller gave up after {MAX_POLLS} attempts (~{MAX_POLLS * POLL_SECONDS}s)", flush=True)
    sentinel_path.write_text(json.dumps({"status": "PollerTimeout", "evalRunId": eval_run_id}))
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]))
