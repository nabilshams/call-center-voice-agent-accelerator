"""Analyze downloaded output_items.jsonl and produce a clean per-row report.

Usage:
    python scripts/analyze_eval_results.py <resultsDir>

Reads:
    <resultsDir>/output_items.jsonl
Writes:
    <resultsDir>/report.md          (human-readable per-row report + failure clusters)
    <resultsDir>/scores.csv         (row x evaluator score matrix)
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path


def extract_text(content):
    """Unwrap the nested JSON content field the OpenAI evals API uses."""
    if content is None:
        return None
    if isinstance(content, str):
        s = content.strip()
        if s.startswith("[") or s.startswith("{"):
            try:
                parsed = json.loads(s)
            except Exception:
                return s
            if isinstance(parsed, list) and parsed:
                first = parsed[0]
                if isinstance(first, dict):
                    return first.get("text") or first.get("content") or json.dumps(first)
                return str(first)
            if isinstance(parsed, dict):
                return parsed.get("text") or parsed.get("content") or json.dumps(parsed)
        return s
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            return first.get("text") or first.get("content")
    return str(content)


def last_assistant_text(messages):
    if not isinstance(messages, list):
        return None
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            return extract_text(msg.get("content"))
    return None


def user_text(messages):
    if not isinstance(messages, list):
        return None
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "user":
            return extract_text(msg.get("content"))
    return None


def judge_reason(result):
    """Pull the judge's freeform reason string from an evaluator result."""
    reason_field = result.get("reason")
    if isinstance(reason_field, str):
        return reason_field
    if isinstance(reason_field, list) and reason_field:
        first = reason_field[0]
        if isinstance(first, dict):
            content = first.get("content")
            if isinstance(content, str):
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict):
                        return parsed.get("reason") or content
                except Exception:
                    return content
            return str(content)
    sample = result.get("sample")
    if isinstance(sample, dict):
        return extract_text(sample.get("output"))
    return None


def main(results_dir: str) -> int:
    d = Path(results_dir)
    items_path = d / "output_items.jsonl"
    if not items_path.exists():
        print(f"Missing {items_path}", file=sys.stderr)
        return 2

    rows = []
    with items_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))

    # Discover all evaluator names.
    evaluator_names = []
    seen = set()
    for row in rows:
        for r in row.get("results") or []:
            name = r.get("name") or r.get("evaluator")
            if name and name not in seen:
                seen.add(name)
                evaluator_names.append(name)

    # Build scored table.
    scored_rows = []
    for row in rows:
        sample = row.get("sample") or {}
        query = user_text(sample.get("input"))
        answer = last_assistant_text(sample.get("output"))
        scores = {}
        reasons = {}
        for r in row.get("results") or []:
            name = r.get("name") or r.get("evaluator")
            scores[name] = {
                "score": r.get("score"),
                "passed": r.get("passed"),
                "label": r.get("label"),
            }
            reasons[name] = judge_reason(r)
        scored_rows.append({
            "id": row.get("id") or row.get("datasource_item_id"),
            "query": query,
            "answer": answer,
            "scores": scores,
            "reasons": reasons,
        })

    # CSV.
    csv_path = d / "scores.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "query"] + [f"{e}_score" for e in evaluator_names] + [f"{e}_passed" for e in evaluator_names])
        for r in scored_rows:
            writer.writerow(
                [r["id"], (r["query"] or "")[:120]]
                + [r["scores"].get(e, {}).get("score") for e in evaluator_names]
                + [r["scores"].get(e, {}).get("passed") for e in evaluator_names]
            )

    # Cluster failures per evaluator.
    fail_clusters = defaultdict(list)
    for r in scored_rows:
        for evaluator, s in r["scores"].items():
            if s.get("passed") is False:
                fail_clusters[evaluator].append({
                    "id": r["id"],
                    "score": s.get("score"),
                    "query": (r["query"] or "")[:150],
                    "answer": (r["answer"] or "")[:250],
                    "reason": (r["reasons"].get(evaluator) or "")[:400],
                })

    # Aggregate.
    n = len(scored_rows)
    aggregates = {}
    for e in evaluator_names:
        scores = [r["scores"].get(e, {}).get("score") for r in scored_rows if isinstance(r["scores"].get(e, {}).get("score"), (int, float))]
        passed = [r["scores"].get(e, {}).get("passed") for r in scored_rows if r["scores"].get(e, {}).get("passed") is not None]
        aggregates[e] = {
            "mean_score": round(sum(scores) / len(scores), 2) if scores else None,
            "pass_rate": round(sum(1 for p in passed if p) / len(passed), 2) if passed else None,
            "n_scored": len(scores),
            "n_pass_evaluated": len(passed),
        }

    # Markdown report.
    report_path = d / "report.md"
    lines = [f"# Eval report: {d.name}", "", f"Rows: **{n}**", "", "## Aggregate scores", "", "| Evaluator | Mean score | Pass rate | Scored | Pass-graded |", "|---|---|---|---|---|"]
    for e in evaluator_names:
        a = aggregates[e]
        lines.append(f"| `{e}` | {a['mean_score']} | {a['pass_rate']} | {a['n_scored']} | {a['n_pass_evaluated']} |")
    lines.append("")
    lines.append("## Per-row scores")
    lines.append("")
    header = "| id | query | " + " | ".join(evaluator_names) + " |"
    sep = "|---|---|" + "---|" * len(evaluator_names)
    lines.append(header)
    lines.append(sep)
    for r in scored_rows:
        cells = []
        for e in evaluator_names:
            s = r["scores"].get(e, {})
            sc = s.get("score")
            p = s.get("passed")
            cell = f"{sc}"
            if p is True:
                cell += " ✓"
            elif p is False:
                cell += " ✗"
            elif s.get("label") == "not_applicable":
                cell = "n/a"
            cells.append(cell)
        q = (r["query"] or "").replace("|", "\\|")[:80]
        lines.append(f"| {r['id']} | {q} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("## Failure clusters")
    lines.append("")
    for evaluator, fails in fail_clusters.items():
        lines.append(f"### `{evaluator}` — {len(fails)} failure(s)")
        lines.append("")
        for f in fails:
            lines.append(f"- **Row {f['id']}** (score={f['score']})")
            lines.append(f"  - Query: {f['query']}")
            lines.append(f"  - Answer: {f['answer']}")
            lines.append(f"  - Judge: {f['reason']}")
            lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"report -> {report_path}")
    print(f"scores -> {csv_path}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
