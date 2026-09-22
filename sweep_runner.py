"""Resumable batch runner for the full sweep. Processes session logs until a
time budget is hit, appending per-session results to sweep_results.jsonl.
Re-run until it prints DONE."""

import json
import sys
import time
from pathlib import Path

from context_inspector.sweep import discover_sessions, sweep_one

RESULTS = Path("sweep_results.jsonl")
BUDGET_SECONDS = 240


def main() -> None:
    sessions = discover_sessions()
    done = set()
    if RESULTS.exists():
        for line in RESULTS.read_text().splitlines():
            if line.strip():
                done.add(json.loads(line)["path"])

    t0 = time.time()
    processed = 0
    with RESULTS.open("a", encoding="utf-8") as out:
        for source, path in sessions:
            if path in done:
                continue
            if time.time() - t0 > BUDGET_SECONDS:
                break
            try:
                r = sweep_one(source, path)
                rec = {
                    "source": r.source,
                    "path": r.path,
                    "total_tokens": r.total_tokens,
                    "category_tokens": r.category_tokens,
                    "n_duplicates": r.n_duplicates,
                    "wasted_tokens": r.wasted_tokens,
                    "signals": r.signals,
                    "tool_tokens": r.tool_tokens,
                    "tool_counts": r.tool_counts,
                }
            except Exception as exc:
                rec = {"source": source, "path": path, "error": str(exc)[:200]}
            out.write(json.dumps(rec) + "\n")
            out.flush()
            processed += 1

    remaining = len(sessions) - len(done) - processed
    print(f"processed this run: {processed}   remaining: {remaining}   total: {len(sessions)}")
    if remaining <= 0:
        print("DONE")


if __name__ == "__main__":
    main()
