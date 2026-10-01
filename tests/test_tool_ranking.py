"""Grouping-logic tests for figure #6 (tool ranking).

These cover the pure-Python parts of ``context_inspector.charts.tool_ranking``
— the shell-family aggregation, ranking order, and ungrouped per-source
preservation — with a synthetic query output; rendering itself is covered by
the determinism checks in the chart pack's golden tests.
"""

from __future__ import annotations

import math
import unittest

from context_inspector.charts.tool_ranking import (
    GROUPED_LABEL,
    SHELL_FAMILY,
    _compact,
    _per_source_ungrouped,
    aggregate_tools,
)


def synthetic_rows() -> list[dict]:
    """A miniature tool_ranking output with the shell family spread across
    harness names and one extreme tokens-per-call outlier."""
    return [
        # source="all" rows (note: shares here are pre-aggregation, as the
        # query emits them; aggregate_tools must recompute them).
        {"source": "all", "tool": "exec", "tokens": 10_000_000,
         "share": 0.40, "calls": 5_000, "tokens_per_call": 2000.0},
        {"source": "all", "tool": "Bash", "tokens": 5_000_000,
         "share": 0.20, "calls": 10_000, "tokens_per_call": 500.0},
        {"source": "all", "tool": "exec_command", "tokens": 2_500_000,
         "share": 0.10, "calls": 1_000, "tokens_per_call": 2500.0},
        {"source": "all", "tool": "shell_command", "tokens": 500_000,
         "share": 0.02, "calls": 250, "tokens_per_call": 2000.0},
        {"source": "all", "tool": "js_repl", "tokens": 4_000_000,
         "share": 0.16, "calls": 50, "tokens_per_call": 80_000.0},
        {"source": "all", "tool": "Read", "tokens": 3_000_000,
         "share": 0.12, "calls": 3_000, "tokens_per_call": 1000.0},
        # Per-source rows must not affect the ranking but must survive
        # ungrouped in the sidecar.
        {"source": "codex", "tool": "exec_command", "tokens": 2_500_000,
         "share": 0.50, "calls": 1_000, "tokens_per_call": 2500.0},
        {"source": "claude-code", "tool": "Bash", "tokens": 5_000_000,
         "share": 0.60, "calls": 10_000, "tokens_per_call": 500.0},
    ]


class TestAggregateTools(unittest.TestCase):
    def setUp(self) -> None:
        self.ranked = aggregate_tools(synthetic_rows())

    def test_shell_family_collapses_to_one_bar(self) -> None:
        grouped = [r for r in self.ranked if r["grouped"]]
        self.assertEqual(len(grouped), 1)
        bar = grouped[0]
        self.assertEqual(bar["tool"], GROUPED_LABEL)
        self.assertEqual(bar["tokens"], 18_000_000)
        self.assertEqual(bar["calls"], 16_250)
        self.assertTrue(math.isclose(bar["tokens_per_call"],
                                     18_000_000 / 16_250))

    def test_no_shell_member_appears_ungrouped(self) -> None:
        labels = {r["tool"] for r in self.ranked}
        self.assertTrue(SHELL_FAMILY.isdisjoint(labels))

    def test_share_recomputed_against_grand_total(self):
        total = 18_000_000 + 4_000_000 + 3_000_000
        shell = next(r for r in self.ranked if r["grouped"])
        self.assertAlmostEqual(shell["share"], 18_000_000 / total)
        for row in self.ranked:
            self.assertGreaterEqual(row["share"], 0.0)
            self.assertLessEqual(row["share"], 1.0)

    def test_sorted_descending_by_tokens(self) -> None:
        tokens = [r["tokens"] for r in self.ranked]
        self.assertEqual(tokens, sorted(tokens, reverse=True))
        self.assertEqual(self.ranked[0]["tool"], GROUPED_LABEL)

    def test_outlier_tok_per_call_survives_grouping(self) -> None:
        js = next(r for r in self.ranked if r["tool"] == "js_repl")
        self.assertAlmostEqual(js["tokens_per_call"], 80_000.0)
        self.assertEqual(js["tokens"], 4_000_000)

    def test_top_n_limits_length(self) -> None:
        rows = synthetic_rows() + [
            {"source": "all", "tool": f"t{i}", "tokens": i,
             "share": 0.0, "calls": 1, "tokens_per_call": float(i)}
            for i in range(1, 30)
        ]
        self.assertLessEqual(len(aggregate_tools(rows)), 10)
        self.assertEqual(len(aggregate_tools(rows, top_n=3)), 3)

    def test_missing_all_rows_raises(self) -> None:
        with self.assertRaises(ValueError):
            aggregate_tools([{"source": "codex", "tool": "exec",
                              "tokens": 1, "share": 1.0, "calls": 1,
                              "tokens_per_call": 1.0}])


class TestPerSourceUngrouped(unittest.TestCase):
    def test_per_source_rows_preserved_verbatim(self) -> None:
        ungrouped = _per_source_ungrouped(synthetic_rows())
        self.assertEqual(sorted(ungrouped), ["claude-code", "codex"])
        codex = ungrouped["codex"]
        self.assertEqual(len(codex), 1)
        self.assertEqual(codex[0]["tool"], "exec_command")
        self.assertEqual(codex[0]["tokens"], 2_500_000)
        self.assertEqual(codex[0]["calls"], 1_000)
        # Sorted by tokens descending within a source.
        claude = ungrouped["claude-code"]
        self.assertEqual([r["tokens"] for r in claude],
                         sorted((r["tokens"] for r in claude), reverse=True))

    def test_all_rows_excluded(self) -> None:
        self.assertEqual(_per_source_ungrouped(
            [{"source": "all", "tool": "exec", "tokens": 1, "share": 1.0,
              "calls": 1, "tokens_per_call": 1.0}]), {})


class TestCompact(unittest.TestCase):
    def test_buckets(self) -> None:
        self.assertEqual(_compact(13_330_296), "13.3M")
        self.assertEqual(_compact(452_371), "452k")
        self.assertEqual(_compact(999), "999")
        self.assertEqual(_compact(72_531.3), "73k")


if __name__ == "__main__":
    unittest.main()
