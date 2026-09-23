"""Runner rendering tests (#3)."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "sweep_results.jsonl"
duckdb_available = importlib.util.find_spec("duckdb") is not None


@unittest.skipUnless(duckdb_available, "duckdb not installed")
class RunnerTest(unittest.TestCase):
    def test_markdown_table_gfm_with_right_aligned_numbers(self):
        from context_inspector.analytics.runner import render_markdown, load_manifest

        entry = next(e for e in load_manifest() if e.name == "window_percentiles")
        md = render_markdown(entry, ["source", "p50"], [("codex", 800.0), ("kimi", None)])
        lines = md.splitlines()
        self.assertIn("| source | p50 |", lines)
        self.assertIn("|---|---:|", lines)  # numeric column right-aligned
        self.assertIn("| codex | 800.0 |", lines)
        self.assertIn("| kimi | — |", lines)  # NULL rendered as em dash

    def test_run_queries_writes_md_and_json(self):
        from context_inspector.analytics import load_results
        from context_inspector.analytics.runner import load_manifest, run_queries

        con = load_results(str(FIXTURE))
        with tempfile.TemporaryDirectory() as tmp:
            summaries = run_queries(con, ["window_percentiles"], Path(tmp))
            md = (Path(tmp) / "window_percentiles.md").read_text()
            data = json.loads((Path(tmp) / "window_percentiles.json").read_text())
        self.assertTrue(md.startswith("# window_percentiles"))
        self.assertEqual({r["source"] for r in data},
                         {"codex", "claude-code", "kimi-code"})
        self.assertEqual(len(summaries), 1)
        self.assertIn("window_percentiles", summaries[0])

    def test_fmt_scalar_rendering(self):
        from context_inspector.analytics.runner import _fmt, _is_number

        self.assertEqual(_fmt(None), "—")
        self.assertEqual(_fmt(800.0), "800.0")
        self.assertEqual(_fmt(-2.5), "-2.5")
        self.assertEqual(_fmt("a|b"), "a\\|b")
        self.assertNotIn("e+", _fmt(276636.8))  # no scientific notation
        self.assertEqual(_fmt(True), "True")
        self.assertFalse(_is_number(True))  # bool column stays left-aligned

    def test_monthly_trend_json_dates_are_iso_strings(self):
        from context_inspector.analytics import load_results
        from context_inspector.analytics.runner import run_queries

        con = load_results(str(FIXTURE))
        with tempfile.TemporaryDirectory() as tmp:
            run_queries(con, ["monthly_trend"], Path(tmp))
            data = json.loads((Path(tmp) / "monthly_trend.json").read_text())
        self.assertEqual({r["month"] for r in data}, {"2026-08-01", "2026-09-01"})
        self.assertTrue(all(isinstance(r["month"], str) for r in data))

    def test_run_query_missing_sql_file_names_query(self):
        from context_inspector.analytics.runner import QueryEntry, run_query

        entry = QueryEntry(name="nope", file="does_not_exist.sql",
                           description="", columns=[])
        with self.assertRaises(FileNotFoundError) as cm:
            run_query(None, entry)
        self.assertIn("'nope'", str(cm.exception))

    def test_run_queries_unknown_name_raises_named_keyerror(self):
        from context_inspector.analytics import load_results
        from context_inspector.analytics.runner import run_queries

        con = load_results(str(FIXTURE))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(KeyError) as cm:
                run_queries(con, ["window_percentiles", "not_a_query"], Path(tmp))
            # validation happens before any query runs — no partial outdir
            self.assertEqual(list(Path(tmp).iterdir()), [])
        self.assertIn("not_a_query", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
