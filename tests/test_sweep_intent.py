"""Sweep-level intent tests (gh#12)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "intent_transcript.json"


def tokens(fixture_map):
    """Tokenizer factory: fixed token counts per call id for hand-computation."""
    def tok(text):
        for cid, n in fixture_map.items():
            if text == f"result-{cid}":
                return n
        return 0
    return tok


class SweepOneIntentTest(unittest.TestCase):
    def test_session_result_carries_intents(self):
        from context_inspector.sweep import sweep_one

        messages = json.loads(FIXTURE.read_text(encoding="utf-8"))
        # rewrite tool contents to the labeled forms the tokenizer keys on
        sizes = {"c1": 120, "c2": 240, "c3": 480}
        for msg in messages:
            if msg.get("role") == "tool":
                msg["content"] = "result-" + msg["tool_call_id"]
        tmp = FIXTURE.with_suffix(".tmp.json")
        tmp.write_text(json.dumps(messages), encoding="utf-8")
        try:
            # source=None: detect_source sniffs the plain OpenAI JSON and
            # passes it through (load_any rejects unknown explicit labels)
            r = sweep_one(None, str(tmp), tokenizer=tokens(sizes))
        finally:
            tmp.unlink()
        self.assertEqual(r.intent_tokens, {"explore": 120, "test-build": 240, "repl": 480})
        self.assertEqual(r.intent_counts, {"explore": 1, "test-build": 1, "repl": 1})
        self.assertEqual(r.tool_tokens, {"Bash": 360, "js_repl": 480})

    def test_render_sections_show_intents(self):
        from context_inspector.sweep import (
            SessionResult, aggregate, render_sweep, to_markdown,
        )

        r = SessionResult(
            source="claude-code", path="/fake/p/s.jsonl", total_tokens=1000,
            category_tokens={"tool result": 1000}, n_duplicates=0,
            wasted_tokens=0,
            tool_tokens={"Bash": 1000}, tool_counts={"Bash": 2},
            intent_tokens={"explore": 700, "test-build": 300},
            intent_counts={"explore": 1, "test-build": 1},
        )
        agg = aggregate([r])
        text = render_sweep([r], agg)
        self.assertIn("tool intents", text)
        self.assertIn("explore", text)
        self.assertIn("test-build", text)
        md = to_markdown([r], agg)
        self.assertIn("explore", md)


if __name__ == "__main__":
    unittest.main()
