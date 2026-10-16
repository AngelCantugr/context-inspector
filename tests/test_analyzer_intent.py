"""Analyzer intent-aggregation tests (gh#12).

Token expectations are hand-computed: the injected tokenizer is len(),
so a tool result's token count is exactly its content length.
"""

from __future__ import annotations

import json
import unittest

from context_inspector.analyzer import analyze, render_text, to_dict

LEN = lambda s: len(s)  # noqa: E731


def _call(cid: str, name: str, arguments: dict) -> dict:
    return {
        "id": cid,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def transcript() -> list[dict]:
    return [
        {"role": "assistant", "content": "", "tool_calls": [
            _call("c1", "Bash", {"command": "cat foo.py"}),
            _call("c2", "Bash", {"command": "rg pattern"}),
            _call("c3", "Bash", {"command": "pytest -x"}),
            _call("c4", "Bash", {"command": "make build"}),
            _call("c5", "js_repl", {"code": "1+1"}),
            _call("c6", "Read", {"path": "foo.py"}),
            _call("c7", "mystery_tool", {}),
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "x" * 100},
        {"role": "tool", "tool_call_id": "c2", "content": "y" * 200},
        {"role": "tool", "tool_call_id": "c3", "content": "z" * 300},
        {"role": "tool", "tool_call_id": "c4", "content": "w" * 400},
        {"role": "tool", "tool_call_id": "c5", "content": "v" * 500},
        {"role": "tool", "tool_call_id": "c6", "content": "u" * 600},
        {"role": "tool", "tool_call_id": "c7", "content": "t" * 700},
        # result whose call id matches no call — intent falls back to other
        {"role": "tool", "tool_call_id": "orphan", "content": "s" * 50},
    ]


class IntentAggregationTest(unittest.TestCase):
    def setUp(self):
        self.report = analyze(transcript(), tokenizer=LEN)

    def test_intent_tokens_hand_computed(self):
        self.assertEqual(
            self.report.intent_tokens,
            {"explore": 900, "test-build": 700, "repl": 500, "other": 750},
        )

    def test_intent_counts_hand_computed(self):
        self.assertEqual(
            self.report.intent_counts,
            {"explore": 3, "test-build": 2, "repl": 1, "other": 2},
        )

    def test_tool_attribution_unchanged(self):
        # Orphan results attribute to "?" — same as before intent wiring.
        self.assertEqual(
            self.report.tool_tokens,
            {"Bash": 1000, "js_repl": 500, "Read": 600,
             "mystery_tool": 700, "?": 50},
        )
        self.assertEqual(
            self.report.tool_counts,
            {"Bash": 4, "js_repl": 1, "Read": 1, "mystery_tool": 1, "?": 1},
        )

    def test_to_dict_carries_intents(self):
        d = to_dict(self.report)
        self.assertEqual(d["intent_tokens"]["explore"], 900)
        self.assertEqual(d["intent_counts"]["repl"], 1)


class IntentRenderTest(unittest.TestCase):
    def test_render_text_shows_top_intents(self):
        report = analyze(transcript(), tokenizer=LEN)
        text = render_text(report)
        self.assertIn("Top intents", text)
        self.assertIn("explore", text)
        self.assertIn("test-build", text)


if __name__ == "__main__":
    unittest.main()
