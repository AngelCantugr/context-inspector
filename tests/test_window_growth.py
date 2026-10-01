"""Pure-logic tests (#7): window-growth jump selection and attribution.

These cover the deterministic helpers only — no matplotlib required (the
byte-identity/determinism of the rendered PNG is verified by the Phase 3
runner against fixed fixtures).
"""

from __future__ import annotations

import unittest

from context_inspector.analyzer import MessageStat, analyze
from context_inspector.charts.window_growth import (
    jump_label,
    resolve_tool_names,
    top_jumps,
)


def _stat(index: int, tokens: int, category: str = "user", name: str = "") -> MessageStat:
    return MessageStat(
        index=index, role="user", name=name, category=category,
        tokens=tokens, cumulative=tokens,
    )


class TopJumpsTest(unittest.TestCase):
    def test_orders_by_size_desc_then_index(self):
        stats = [_stat(0, 10), _stat(1, 500), _stat(2, 500), _stat(3, 40)]
        jumps = top_jumps(stats, 3)
        self.assertEqual([(s.index, s.tokens) for s in jumps],
                         [(1, 500), (2, 500), (3, 40)])

    def test_fewer_than_three(self):
        stats = [_stat(0, 10), _stat(1, 20)]
        self.assertEqual(len(top_jumps(stats, 3)), 2)


class JumpLabelTest(unittest.TestCase):
    def test_tool_name_attributed(self):
        stat = _stat(7, 12345, category="tool result", name="Bash")
        self.assertEqual(jump_label(stat), "+12,345\ntool result: Bash")

    def test_bare_tool_name_means_category_only(self):
        for bare in ("", "?", "tool", "  tool  "):
            stat = _stat(7, 100, category="tool result", name=bare)
            self.assertEqual(jump_label(stat), "+100\ntool result")

    def test_non_tool_category_ignores_name(self):
        stat = _stat(7, 100, category="assistant", name="anything")
        self.assertEqual(jump_label(stat), "+100\nassistant")

    def test_resolved_name_wins_over_bare_stat_name(self):
        stat = _stat(7, 100, category="tool result", name="tool")
        self.assertEqual(jump_label(stat, "js_repl"), "+100\ntool result: js_repl")


class ResolveToolNamesTest(unittest.TestCase):
    def test_resolves_via_tool_call_id(self):
        messages = [
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "call_1", "type": "function",
                             "function": {"name": "js_repl", "arguments": "{}"}}]},
            {"role": "tool", "content": "huge", "tool_call_id": "call_1",
             "name": "tool"},
        ]
        self.assertEqual(resolve_tool_names(messages), {1: "js_repl"})

    def test_unmatched_call_id_stays_unattributed(self):
        messages = [{"role": "tool", "content": "x", "name": "tool"}]
        self.assertEqual(resolve_tool_names(messages), {})

    def test_end_to_end_with_analyzer(self):
        # The analyzer's MessageStat.name is bare "tool" for this codex-style
        # message; resolution still credits the tool that produced the result.
        messages = [
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "c9", "type": "function",
                             "function": {"name": "Read", "arguments": "{}"}}]},
            {"role": "tool", "content": "payload", "tool_call_id": "c9",
             "name": "tool"},
        ]
        report = analyze(messages)
        names = resolve_tool_names(messages)
        biggest = top_jumps(report.messages, 1)[0]
        self.assertEqual(jump_label(biggest, names.get(biggest.index)),
                         f"+{biggest.tokens:,}\ntool result: Read")


if __name__ == "__main__":
    unittest.main()
