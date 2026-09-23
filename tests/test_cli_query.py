"""CLI smoke test for the query subcommand (#3)."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from context_inspector.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "sweep_results.jsonl"
duckdb_available = importlib.util.find_spec("duckdb") is not None


@unittest.skipUnless(duckdb_available, "duckdb not installed")
class QueryCliTest(unittest.TestCase):
    def test_query_all_into_tempdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = main(["query", str(FIXTURE), "--out", tmp])
            self.assertEqual(rc, 0)
            out = Path(tmp)
            names = [p.stem for p in out.glob("*.md")]
            self.assertEqual(len(names), 10)
            data = json.loads((out / "duplicate_burden.json").read_text())
            total = next(r for r in data if r["source"] == "all")
            self.assertEqual(total["wasted_tokens"], 1500)

    def test_query_single(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = main(["query", str(FIXTURE), "--query", "whale_table", "--out", tmp])
            self.assertEqual(rc, 0)
            self.assertEqual([p.name for p in Path(tmp).glob("*.md")],
                             ["whale_table.md"])

    def test_unknown_query_name_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = main(["query", str(FIXTURE), "--query", "nope", "--out", tmp])
            self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
