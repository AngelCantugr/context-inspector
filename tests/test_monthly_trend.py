"""Figure rendering tests (#8): monthly trend, two panels over calendar month."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from context_inspector.charts.monthly_trend import (
    _prep_by_source,
    _prep_overview,
    render_monthly_trend,
)

mpl_available = importlib.util.find_spec("matplotlib") is not None

# Mirrors the shape of the monthly_trend / monthly_trend_by_source query
# outputs: a sparse early month (codex alone, 1 session -> gap), a month
# where claude-code has exactly 3 sessions (threshold boundary), and a
# kimi-code row below the threshold that must stay a gap.
MONTHLY_ROWS = [
    {"month": "2026-07-01", "sessions": 1, "total_tokens": 500,
     "median_window": 500.0},
    {"month": "2026-08-01", "sessions": 7, "total_tokens": 21000,
     "median_window": 3000.0},
    {"month": "2026-09-01", "sessions": 6, "total_tokens": 12000,
     "median_window": 2000.0},
]
BY_SOURCE_ROWS = [
    {"source": "codex", "month": "2026-07-01", "sessions": 1,
     "total_tokens": 500, "median_window": 500.0},
    {"source": "codex", "month": "2026-08-01", "sessions": 3,
     "total_tokens": 9000, "median_window": 3000.0},
    {"source": "claude-code", "month": "2026-08-01", "sessions": 3,
     "total_tokens": 10000, "median_window": 3500.0},
    {"source": "claude-code", "month": "2026-09-01", "sessions": 4,
     "total_tokens": 10000, "median_window": 2500.0},
    {"source": "kimi-code", "month": "2026-09-01", "sessions": 2,
     "total_tokens": 2000, "median_window": 1000.0},
]


MONTHS = ["2026-07", "2026-08", "2026-09"]


class PrepOverviewTest(unittest.TestCase):
    def test_sorts_rows_by_month_regardless_of_input_order(self):
        shuffled = [MONTHLY_ROWS[2], MONTHLY_ROWS[0], MONTHLY_ROWS[1]]
        self.assertEqual(
            [r["month"] for r in _prep_overview(shuffled)],
            ["2026-07", "2026-08", "2026-09"],
        )

    def test_drops_rows_without_month(self):
        rows = MONTHLY_ROWS + [{"month": None, "sessions": 9,
                                "total_tokens": 9, "median_window": 9.0}]
        self.assertEqual(len(_prep_overview(rows)), 3)


class PrepBySourceTest(unittest.TestCase):
    def test_month_below_threshold_is_a_gap_not_zero(self):
        prepared = _prep_by_source(BY_SOURCE_ROWS, MONTHS, min_sessions=3)
        # codex 2026-07 has 1 session -> None (gap), never median 0.0.
        self.assertIsNone(prepared["codex"][0])
        # 2026-08 has exactly 3 sessions -> drawn (boundary is inclusive).
        self.assertEqual(prepared["codex"][1]["month"], "2026-08")
        self.assertEqual(prepared["codex"][1]["median_window"], 3000.0)
        # codex has no 2026-09 row at all -> also a gap.
        self.assertIsNone(prepared["codex"][2])

    def test_absent_month_is_indistinguishable_from_below_threshold(self):
        prepared = _prep_by_source(BY_SOURCE_ROWS, MONTHS, min_sessions=3)
        # claude-code has no 2026-07 row; kimi-code's 2026-09 row has only
        # 2 sessions — both must be gaps.
        self.assertIsNone(prepared["claude-code"][0])
        self.assertIsNone(prepared["kimi-code"][2])
        # kimi-code never reaches 3 sessions in any month -> all gaps.
        self.assertTrue(all(s is None for s in prepared["kimi-code"]))


@unittest.skipUnless(mpl_available, "matplotlib not installed")
class MonthlyTrendTest(unittest.TestCase):
    def _render(self, tmp: str, **kwargs):
        out = Path(tmp) / "figs"
        png = render_monthly_trend(
            MONTHLY_ROWS, BY_SOURCE_ROWS, out, **kwargs
        )
        sidecar = json.loads((out / "monthly_trend.json").read_text())
        return png, sidecar

    def test_renders_png_and_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            png, sidecar = self._render(tmp, data_date="2026-09-20")
            self.assertTrue(png.exists())
            self.assertGreater(png.stat().st_size, 5000)
            self.assertEqual(sidecar["months"], ["2026-07", "2026-08",
                                                 "2026-09"])
            self.assertEqual(sidecar["min_sessions"], 3)
            self.assertEqual(
                [s["source"] for s in sidecar["sources"]],
                ["claude-code", "codex"],  # SOURCE_ORDER; kimi-code all-gap
            )

    def test_sidecar_points_reflect_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, sidecar = self._render(tmp)
            codex = next(s for s in sidecar["sources"]
                         if s["source"] == "codex")
            self.assertEqual(
                [(p["month"], p["sessions"]) for p in codex["points"]],
                [("2026-08", 3)],  # 1-session 2026-07 is a gap, not a point
            )

    def test_regeneration_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            png1, sidecar1 = self._render(tmp, data_date="2026-09-20")
            png2, sidecar2 = self._render(tmp, data_date="2026-09-20")
            self.assertEqual(png1.read_bytes(), png2.read_bytes())
            self.assertEqual(sidecar1, sidecar2)

    def test_sparse_first_month_and_single_month_do_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "figs"
            # Sparse first month: only a 1-session codex row.
            png = render_monthly_trend(
                [MONTHLY_ROWS[0]],
                [BY_SOURCE_ROWS[0]],
                out,
            )
            self.assertTrue(png.exists())
            # Single month total, empty by-source output.
            png = render_monthly_trend([MONTHLY_ROWS[1]], [], out)
            self.assertTrue(png.exists())
            # No by-source rows passing the threshold -> no source lines.
            png = render_monthly_trend(MONTHLY_ROWS, BY_SOURCE_ROWS[:1], out)
            self.assertTrue(png.exists())

    def test_empty_monthly_rows_raise_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                render_monthly_trend([], BY_SOURCE_ROWS, Path(tmp))


if __name__ == "__main__":
    unittest.main()
