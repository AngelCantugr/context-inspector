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


if __name__ == "__main__":
    unittest.main()
