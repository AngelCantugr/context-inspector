"""Golden-file test of a full fake week (#16).

One end-to-end run of the memo/filing stage against checked-in fixtures:

- deterministic stages: previous snapshot (sweep_weekA.jsonl) seeded in the
  vault root, ``run_weekly`` snapshots/diffs/scores the current week
  (sweep_weekB.jsonl) into the deepdive manifest;
- interpretation: the LangGraph agent runs over the manifest with a canned
  fake Anthropic client; each deep dive gathers evidence via
  ``run_query("whale_table")`` and cites T1 (week-level evidence is shared
  across deep dives);
- filing: the note written into the vault reports folder must be
  byte-identical to the checked-in expected note.

Determinism: tool arguments (``{"name": "whale_table"}``) and the query
result depend only on fixture data and fully-ordered SQL, so no temp-dir
paths leak into the rendered note and the golden file is stable across
machines. Regenerate with ``UPDATE_GOLDEN=1`` (review the diff before
committing — a golden change must always be a deliberate template change).
"""

from __future__ import annotations

import difflib
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from context_inspector.weekly import run_weekly

FIXTURES = Path(__file__).parent / "fixtures"
WEEK_A = FIXTURES / "sweep_weekA.jsonl"  # previous week snapshot
WEEK_B = FIXTURES / "sweep_weekB.jsonl"  # current week sweep results
GOLDEN = FIXTURES / "context-hygiene-weekly-2026-W42.md"
TODAY = date(2026, 10, 15)  # ISO week 2026-W42

INTERPRET = {
    "trend_narrative": (
        "Tool-result bloat is the through-line: all three outliers carry "
        "duplicate-heavy tool output, and the two growing sessions grew "
        "almost entirely in the tool-result category."
    ),
    "experiment": (
        "For one week, truncate tool results older than the last two calls "
        "in every session and re-run the sweep — if wasted_tokens drops "
        "week-over-week, adopt the truncation as a standing habit."
    ),
}


def _finding(pattern: str, cause: str, prescription: str) -> str:
    return json.dumps(
        {
            "pattern": pattern,
            "likely_cause": cause,
            "prescription": prescription,
            "citations": ["T1"],
        }
    )


def _text_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=text)],
    )


def _tool_use_response(name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(
                type="tool_use", id="toolu_1", name=name, input=arguments
            )
        ],
    )


class FakeMessages:
    def __init__(self, script: list):
        self.script = list(script)

    def create(self, **kwargs):
        if not self.script:
            raise AssertionError("fake client ran out of canned responses")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, script: list):
        self.messages = FakeMessages(script)


class GoldenMemoTest(unittest.TestCase):
    def test_full_fake_week_matches_golden_note(self):
        from context_inspector.agent import AgentConfig, run_interpretation

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            # Stage 1 — deterministic weekly stages over the fixtures.
            snapshots_dir = root / "reports" / "snapshots"
            snapshots_dir.mkdir(parents=True)
            (snapshots_dir / "sweep-2026-10-08.jsonl").write_text(
                WEEK_A.read_text(encoding="utf-8"), encoding="utf-8"
            )
            (root / "sweep_results.jsonl").write_text(
                WEEK_B.read_text(encoding="utf-8"), encoding="utf-8"
            )
            manifest = run_weekly(today=TODAY, root=root, run_sweep=lambda: None)
            self.assertFalse(manifest["baseline"])
            self.assertEqual(len(manifest["outliers"]), 3)

            # Stage 2 — interpretation over the manifest, model canned:
            # one run_query deep dive per outlier, then the interpret stage.
            canned: list = []
            for outlier in manifest["outliers"]:
                canned.append(_tool_use_response("run_query", {"name": "whale_table"}))
                canned.append(
                    _text_response(
                        _finding(
                            pattern=f"{outlier['source']} tool-result bloat",
                            cause=(
                                f"{outlier['path']} carries "
                                f"{outlier['signal_count']} bloat signal(s) "
                                f"and {outlier['wasted_ratio']:.0%} wasted tokens"
                            ),
                            prescription=(
                                "deduplicate repeated tool outputs before "
                                "they are re-injected into the window"
                            ),
                        )
                    )
                )
            canned.append(_text_response(json.dumps(INTERPRET)))
            client = FakeClient(canned)

            memo = run_interpretation(
                manifest,
                AgentConfig(
                    root=root,
                    notes_dir=root / "vault" / "reports",
                    client=client,
                    backoff_seconds=0,
                ),
            )

            # Stage 3 — the filed note matches the checked-in golden note.
            note_path = Path(memo.filed_path)
            self.assertEqual(
                note_path.name, "context-hygiene-weekly-2026-W42.md"
            )
            actual = note_path.read_text(encoding="utf-8")
            if os.environ.get("UPDATE_GOLDEN"):
                GOLDEN.write_text(actual, encoding="utf-8")
            expected = GOLDEN.read_text(encoding="utf-8")
            if actual != expected:
                diff = difflib.unified_diff(
                    expected.splitlines(),
                    actual.splitlines(),
                    fromfile=str(GOLDEN),
                    tofile="actual",
                    lineterm="",
                )
                self.fail(
                    "filed note diverged from the golden note:\n"
                    + "\n".join(diff)
                )


if __name__ == "__main__":
    unittest.main()
