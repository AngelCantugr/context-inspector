"""CLI smoke test for the query subcommand (#3)."""

from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
            self.assertEqual(len(names), 11)
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

    def test_corrupt_jsonl_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "corrupt.jsonl"
            bad.write_text('{"source": "claude", "path": "/x", "total',
                           encoding="utf-8")
            with redirect_stderr(io.StringIO()) as err:
                rc = main(["query", str(bad), "--out", tmp])
            self.assertEqual(rc, 1)
            self.assertTrue(err.getvalue().startswith("error: "))

    def test_missing_file_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stderr(io.StringIO()) as err:
                rc = main(["query", str(Path(tmp) / "nope.jsonl"), "--out", tmp])
            self.assertEqual(rc, 1)
            self.assertTrue(err.getvalue().startswith("error: "))

    def test_json_flag_prints_full_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()) as out:
                rc = main(["query", str(FIXTURE), "--query", "window_percentiles",
                           "--json", "--out", tmp])
            self.assertEqual(rc, 0)
            payload = out.getvalue().partition("\n")[2]  # skip summary line
            data = json.loads(payload)
            self.assertTrue(data)
            self.assertTrue(all("source" in row for row in data))


if __name__ == "__main__":
    unittest.main()
