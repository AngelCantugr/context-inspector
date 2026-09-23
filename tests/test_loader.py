"""Loader tests (#1). Expected values are hand-computed from
tests/fixtures/sweep_results.jsonl — see FIXTURE.md notes in test_queries."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "sweep_results.jsonl"

duckdb_available = importlib.util.find_spec("duckdb") is not None


@unittest.skipUnless(duckdb_available, "duckdb not installed (pip install '.[analytics]')")
class LoaderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from context_inspector.analytics import load_results

        cls.con = load_results(str(FIXTURE))

    def test_sessions_row_count(self):
        (n,) = self.con.sql("select count(*) from sessions").fetchone()
        self.assertEqual(n, 10)

    def test_categories_view_unnested(self):
        (n,) = self.con.sql("select count(*) from categories").fetchone()
        self.assertEqual(n, 19)

    def test_tools_view_unnested(self):
        (n,) = self.con.sql("select count(*) from tools").fetchone()
        self.assertEqual(n, 7)

    def test_old_record_without_tool_fields_loads(self):
        # record 8 (kimi-code) has no tool_tokens/tool_counts
        (n,) = self.con.sql(
            "select count(*) from tools where source = 'kimi-code' and calls is null"
        ).fetchone()
        self.assertEqual(n, 0)  # no tool rows at all for that session
        rows = self.con.sql(
            "select path, tokens, calls from tools where source = 'kimi-code'"
        ).fetchall()
        self.assertEqual(rows, [("/fake/kimi/sessions/ws1/conv1/agents/main/wire.jsonl", 450, 3)])

    def test_error_stub_loads_with_null_metrics(self):
        row = self.con.sql(
            "select total_tokens, error from sessions where error is not null"
        ).fetchall()
        self.assertEqual(row, [(None, "boom")])

    def test_session_date_path_derived(self):
        rows = self.con.sql(
            "select session_date from sessions where path like '%rollout-a1%'"
        ).fetchall()
        self.assertEqual(str(rows[0][0]), "2026-08-05")


class FriendlyErrorTest(unittest.TestCase):
    def test_loader_import_never_breaks_base_cli(self):
        # base CLI must import and run with no analytics deps touched
        import context_inspector.cli  # noqa: F401

        self.assertTrue(callable(context_inspector.cli.main))


if __name__ == "__main__":
    unittest.main()
