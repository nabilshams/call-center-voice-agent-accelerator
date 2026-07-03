"""Download per-row output_items for a completed Foundry eval run.

Per the Microsoft Foundry observe skill: `evaluation_get` MCP tool
returns only run-level metadata. Per-row scores + agent responses
require the `azure-ai-projects` Python SDK path.

Usage:
    python scripts/download_eval_results.py <projectEndpoint> <evalId> <evalRunId> <outputDir>

Writes:
    <outputDir>/output_items.jsonl      (raw per-row records)
    <outputDir>/summary.json            (compact scored summary)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential


def main(project_endpoint: str, eval_id: str, eval_run_id: str, output_dir: str) -> int:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    credential = AzureCliCredential(process_timeout=60)
    project = AIProjectClient(endpoint=project_endpoint, credential=credential)

    openai_client = project.get_openai_client()

    items_path = out / "output_items.jsonl"
    summary_path = out / "summary.json"

    per_row = []
    with items_path.open("w", encoding="utf-8") as f:
        pager = openai_client.evals.runs.output_items.list(
            eval_id=eval_id,
            run_id=eval_run_id,
        )
        for item in pager:
            record = item.model_dump() if hasattr(item, "model_dump") else dict(item)
            f.write(json.dumps(record, default=str) + "\n")
            per_row.append(record)

    print(f"downloaded {len(per_row)} rows -> {items_path}", flush=True)

    # Build a compact per-row summary: query + judge results.
    compact = []
    for row in per_row:
        sample = row.get("sample") or {}
        input_items = sample.get("input") if isinstance(sample, dict) else None
        query = None
        if isinstance(input_items, list):
            for it in input_items:
                if isinstance(it, dict) and it.get("role") == "user":
                    query = it.get("content")
                    break
        results = row.get("results") or []
        scored = {}
        for r in results:
            if not isinstance(r, dict):
                continue
            name = r.get("name") or r.get("evaluator") or "?"
            scored[name] = {
                "score": r.get("score"),
                "passed": r.get("passed"),
                "label": r.get("label"),
                "reason": (r.get("sample") or {}).get("output") if isinstance(r.get("sample"), dict) else r.get("reason"),
            }
        output = sample.get("output") if isinstance(sample, dict) else None
        answer = None
        if isinstance(output, list) and output:
            first = output[0]
            if isinstance(first, dict):
                answer = first.get("content")
        compact.append({
            "id": row.get("id"),
            "query": query,
            "answer": answer,
            "results": scored,
        })

    summary_path.write_text(json.dumps(compact, indent=2, default=str), encoding="utf-8")
    print(f"summary -> {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]))
