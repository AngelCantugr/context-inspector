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

    def test_tool_ranking(self):
        rows = {r[1]: r for r in self.query("tool_ranking") if r[0] == "all"}
        self.assertEqual(rows["exec"][2:6], (1680, 1680 / 4030, 8, 210.0))
        self.assertEqual(rows["Bash"][2:6], (1750, 1750 / 4030, 9, 1750 / 9))
        self.assertEqual(rows["apply_patch"][2:6], (200, 200 / 4030, 2, 100.0))

    def test_whale_table(self):
        rows = self.query("whale_table")
        self.assertEqual(len(rows), 8)  # error stub + zero-token excluded
        self.assertEqual(rows[0][1:4],
                         ("/fake/claude/projects/p1/s1.jsonl", "claude-code", 4000))

    def test_duplicate_burden(self):
        rows = {r[0]: r for r in self.query("duplicate_burden")}
        self.assertEqual(rows["all"][1:4], (9, 3, 1500))
        self.assertAlmostEqual(rows["all"][4], 1500 / 9400, places=4)
        self.assertEqual(rows["kimi-code"][1:4], (2, 1, 450))

    def test_whale_table_limit_param(self):
        from context_inspector.analytics.runner import run_query
        entry = self.manifest["whale_table"]
        columns, rows = run_query(self.con, entry, [2])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][3], 4000)

    def test_division_edges_yield_null_not_nan_inf(self):
        import json, tempfile
        from context_inspector.analytics import load_results
        from context_inspector.analytics.runner import run_query
        recs = [
            {"source": "zero-src", "path": "/fake/z/sessions/2026/01/01/z.jsonl",
             "total_tokens": 0, "category_tokens": {}, "n_duplicates": 0,
             "wasted_tokens": 0, "signals": [], "tool_tokens": {}, "tool_counts": {}},
            {"source": "odd", "path": "/fake/o/sessions/2026/01/02/o.jsonl",
             "total_tokens": 10, "category_tokens": {"tool result": 10},
             "n_duplicates": 0, "wasted_tokens": 0, "signals": [],
             "tool_tokens": {"exec": 10}, "tool_counts": {"exec": 0}},
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
            tmp = f.name
        con = load_results(tmp)
        burden = {r[0]: r for r in run_query(con, self.manifest["duplicate_burden"])[1]}
        self.assertIsNone(burden["zero-src"][4])
        ranking = {r[1]: r for r in run_query(con, self.manifest["tool_ranking"])[1]
                   if r[0] == "odd"}
        self.assertIsNone(ranking["exec"][5])


if __name__ == "__main__":
    unittest.main()
