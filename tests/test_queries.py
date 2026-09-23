"""Query-suite tests (#2). Every expected value below is hand-computed from
tests/fixtures/sweep_results.jsonl (see the golden table in
docs/plans/2026-09-22-l1-analytics-plan.md, Task 2)."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "sweep_results.jsonl"
duckdb_available = importlib.util.find_spec("duckdb") is not None


@unittest.skipUnless(duckdb_available, "duckdb not installed")
class QueryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from context_inspector.analytics import load_results
        from context_inspector.analytics.runner import load_manifest, run_query

        cls.con = load_results(str(FIXTURE))
        cls.manifest = {e.name: e for e in load_manifest()}
        # Named run_query, not run: TestCase.run is the unittest entry point.
        cls.run_query = staticmethod(run_query)

    def query(self, name):
        entry = self.manifest[name]
        columns, rows = self.run_query(self.con, entry)
        self.assertEqual(list(columns), entry.columns)
        return rows

    def test_manifest_covers_all_ten_queries(self):
        expected = {
            "monthly_trend", "window_percentiles", "category_shares",
            "tool_ranking", "whale_table", "duplicate_burden",
            "tool_share_trend", "signal_frequency", "category_outliers",
            "bloat_signals_by_tool",
        }
        self.assertEqual(set(self.manifest), expected)

    def test_monthly_trend(self):
        rows = self.query("monthly_trend")
        self.assertEqual(
            [(str(r[0]), r[1], r[2], r[3]) for r in rows],
            [("2026-08-01", 2, 1500, 750.0), ("2026-09-01", 2, 2600, 1300.0)],
        )

    def test_window_percentiles_median_excludes_empty(self):
        rows = {r[0]: r for r in self.query("window_percentiles")}
        self.assertEqual(rows["codex"][1], 800.0)
        self.assertEqual(rows["claude-code"][1], 2050.0)
        self.assertEqual(rows["kimi-code"][1], 600.0)

    def test_category_shares_token_weighted_with_grand_total(self):
        rows = self.query("category_shares")
        codex_tool = next(r for r in rows if r[0] == "codex" and r[1] == "tool result")
        self.assertEqual(codex_tool[2], 1880)
        self.assertAlmostEqual(codex_tool[3], 1880 / 4100, places=4)
        grand = next(r for r in rows if r[0] == "all" and r[1] == "tool result")
        self.assertEqual(grand[2], 4030)
        self.assertAlmostEqual(grand[3], 4030 / 9400, places=4)


if __name__ == "__main__":
    unittest.main()
