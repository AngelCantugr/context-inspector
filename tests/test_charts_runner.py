"""Golden tests for the charts runner (#9).

The three query-driven figures render from small static JSON fixtures under
``tests/fixtures/query_outputs/`` — those files were generated once by
running the real L1 queries (``category_shares``, ``tool_ranking``,
``monthly_trend``, ``monthly_trend_by_source``) over
``tests/fixtures/sweep_results.jsonl`` via ``analytics.runner.run_queries``
and checking in the resulting ``<name>.json`` verbatim, so the golden tests
stay hermetic (no duckdb needed) while exercising the exact query-output
shape the runner sees in production. ``window_growth`` renders from the
checked-in ``examples/sample_transcript.json``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from context_inspector.charts.runner import (
    DEFAULT_QUERIES_DIR,
    FIGURE_NAMES,
    run_charts,
)
from context_inspector.cli import main

mpl_available = importlib.util.find_spec("matplotlib") is not None
duckdb_available = importlib.util.find_spec("duckdb") is not None

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_QUERIES = Path(__file__).parent / "fixtures" / "query_outputs"
SAMPLE_TRANSCRIPT = REPO_ROOT / "examples" / "sample_transcript.json"
FIXTURE_SWEEP = Path(__file__).parent / "fixtures" / "sweep_results.jsonl"

QUERY_FIGURES = ("category_composition", "tool_ranking", "monthly_trend")
ALL_FIGURES = list(FIGURE_NAMES)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(mpl_available, "matplotlib not installed")
class GoldenRenderTest(unittest.TestCase):
    """Each figure renders from checked-in fixture data; renders are stable."""

    def _render(self, tmp: str, figures=ALL_FIGURES):
        out = Path(tmp) / "figs"
        rendered = run_charts(
            figures=list(figures),
            session=str(SAMPLE_TRANSCRIPT),
            out_dir=out,
            queries_dir=FIXTURE_QUERIES,
        )
        return out, dict(rendered)

    def test_all_figures_render_from_fixtures(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, rendered = self._render(tmp)
            self.assertEqual(sorted(rendered), sorted(ALL_FIGURES))
            for figure, png in rendered.items():
                self.assertEqual(png, out / f"{figure}.png")
                self.assertTrue(png.exists())
                self.assertGreater(png.stat().st_size, 5000)  # non-trivial image
                sidecar = json.loads((out / f"{figure}.json").read_text())
                self.assertEqual(sidecar["figure"], figure)

    def test_data_date_derived_from_monthly_trend(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, _ = self._render(tmp)
            for figure in ALL_FIGURES:
                sidecar = json.loads((out / f"{figure}.json").read_text())
                self.assertEqual(sidecar["data_date"], "2026-09")

    def test_regeneration_is_byte_identical(self):
        """Two fresh renders into separate dirs: all four PNGs sha256-equal."""
        with tempfile.TemporaryDirectory() as tmp1, \
                tempfile.TemporaryDirectory() as tmp2:
            out1, _ = self._render(tmp1)
            out2, _ = self._render(tmp2)
            for figure in ALL_FIGURES:
                self.assertEqual(
                    _sha256(out1 / f"{figure}.png"),
                    _sha256(out2 / f"{figure}.png"),
                    figure,
                )
            self.assertEqual(
                sorted(p.name for p in out1.iterdir()),
                sorted(p.name for p in out2.iterdir()),
            )


@unittest.skipUnless(mpl_available, "matplotlib not installed")
class ChartsCliTest(unittest.TestCase):
    """CLI-level behavior of the charts subcommand."""

    def test_window_growth_without_session_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stderr(io.StringIO()) as err:
                rc = main(["charts", "--figure", "window_growth",
                           "--out", tmp])
            self.assertEqual(rc, 1)
            self.assertIn(
                "error: figure 'window_growth' requires --session <log>",
                err.getvalue(),
            )

    def test_unknown_figure_fails_listing_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stderr(io.StringIO()) as err:
                rc = main(["charts", "--figure", "nope", "--out", tmp])
            self.assertEqual(rc, 2)
            msg = err.getvalue()
            self.assertIn("unknown figure 'nope'", msg)
            for name in FIGURE_NAMES:
                self.assertIn(name, msg)

    def test_unknown_sweep_path_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.jsonl"
            with redirect_stderr(io.StringIO()) as err:
                rc = main(["charts", "--figure", "tool_ranking",
                           "--sweep", str(missing), "--out", tmp])
            self.assertEqual(rc, 1)
            self.assertIn(f"sweep results file not found: {missing}",
                          err.getvalue())

    def test_missing_query_output_names_file_and_remedy(self):
        # No --sweep and no reports/queries/ in the cwd: the error must name
        # the missing file and how to produce it.
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with redirect_stderr(io.StringIO()) as err:
                    rc = main(["charts", "--figure", "category_composition",
                               "--out", "figs"])
            finally:
                os.chdir(cwd)
            self.assertEqual(rc, 1)
            msg = err.getvalue()
            self.assertIn("reports/queries/category_shares.json", msg)
            self.assertIn("--sweep", msg)

    def test_charts_without_sweep_reads_reports_queries(self):
        # In a temp cwd with reports/queries/ populated from the fixtures,
        # the three query-driven figures render with no duckdb involved.
        with tempfile.TemporaryDirectory() as tmp:
            queries_dir = Path(tmp) / DEFAULT_QUERIES_DIR
            queries_dir.mkdir(parents=True)
            for src in FIXTURE_QUERIES.glob("*.json"):
                shutil.copy(src, queries_dir / src.name)
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                figure_args = []
                for figure in QUERY_FIGURES:
                    figure_args += ["--figure", figure]
                with redirect_stdout(io.StringIO()):
                    rc = main(["charts", *figure_args, "--out", "figs"])
            finally:
                os.chdir(cwd)
            self.assertEqual(rc, 0)
            for figure in QUERY_FIGURES:
                self.assertTrue((Path(tmp) / "figs" / f"{figure}.png").exists())

    def test_default_charts_skips_window_growth_without_session(self):
        # Default figure set without --session: window_growth arrives only
        # via the default, so it is skipped with a stderr note (not a hard
        # error) and the other three figures still render.
        with tempfile.TemporaryDirectory() as tmp:
            queries_dir = Path(tmp) / DEFAULT_QUERIES_DIR
            queries_dir.mkdir(parents=True)
            for src in FIXTURE_QUERIES.glob("*.json"):
                shutil.copy(src, queries_dir / src.name)
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with redirect_stdout(io.StringIO()), \
                        redirect_stderr(io.StringIO()) as err:
                    rc = main(["charts", "--out", "figs"])
            finally:
                os.chdir(cwd)
            self.assertEqual(rc, 0)
            self.assertIn(
                "skipping window_growth (requires --session <log>)",
                err.getvalue(),
            )
            for figure in QUERY_FIGURES:
                self.assertTrue((Path(tmp) / "figs" / f"{figure}.png").exists())
            self.assertFalse(
                (Path(tmp) / "figs" / "window_growth.png").exists()
            )

    def test_window_growth_from_sample_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                rc = main(["charts", "--figure", "window_growth",
                           "--session", str(SAMPLE_TRANSCRIPT),
                           "--out", tmp])
            self.assertEqual(rc, 0)
            self.assertTrue((Path(tmp) / "window_growth.png").exists())

    def test_missing_session_log_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.jsonl"
            with redirect_stderr(io.StringIO()) as err:
                rc = main(["charts", "--figure", "window_growth",
                           "--session", str(missing), "--out", tmp])
            self.assertEqual(rc, 1)
            self.assertIn("session log not found", err.getvalue())


@unittest.skipUnless(mpl_available and duckdb_available,
                     "matplotlib/duckdb not installed")
class ChartsSweepCliTest(unittest.TestCase):
    """End-to-end: --sweep re-runs the L1 queries and renders all figures."""

    def test_sweep_run_renders_all_four(self):
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                rc = main(["charts", "--sweep", str(FIXTURE_SWEEP),
                           "--session", str(SAMPLE_TRANSCRIPT),
                           "--out", tmp])
            self.assertEqual(rc, 0)
            for figure in ALL_FIGURES:
                png = Path(tmp) / f"{figure}.png"
                self.assertTrue(png.exists())
                self.assertGreater(png.stat().st_size, 5000)


if __name__ == "__main__":
    unittest.main()
