"""Weekly orchestrator tests (#14): snapshot, diff, outlier manifest."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from context_inspector.weekly import (
    WeeklyError,
    diff_snapshots,
    find_snapshots,
    load_results,
    prune_snapshots,
    run_weekly,
    score_sessions,
    take_snapshot,
)

FIXTURES = Path(__file__).parent / "fixtures"
WEEK_A = FIXTURES / "sweep_weekA.jsonl"  # previous week
WEEK_B = FIXTURES / "sweep_weekB.jsonl"  # current week
TODAY = date(2026, 10, 15)
LAST_WEEK = date(2026, 10, 8)


def _write_results(root: Path, fixture: Path) -> None:
    (root / "sweep_results.jsonl").write_text(
        fixture.read_text(encoding="utf-8"), encoding="utf-8"
    )


def _fake_sweep(root: Path, fixture: Path):
    def run() -> None:
        _write_results(root, fixture)

    return run


class SnapshotTest(unittest.TestCase):
    def test_snapshot_copies_results_with_dated_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_results(root, WEEK_B)
            snapshot = take_snapshot(TODAY, root)
            self.assertEqual(snapshot.name, "sweep-2026-10-15.jsonl")
            self.assertEqual(snapshot.read_bytes(), WEEK_B.read_bytes())

    def test_same_day_rerun_is_idempotent_and_prunes_to_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshots_dir = root / "reports" / "snapshots"
            snapshots_dir.mkdir(parents=True)
            (snapshots_dir / "sweep-2026-10-01.jsonl").write_text("{}\n")
            (snapshots_dir / "sweep-2026-10-08.jsonl").write_text("{}\n")

            _write_results(root, WEEK_A)
            take_snapshot(LAST_WEEK, root)  # same-date re-run
            _write_results(root, WEEK_B)
            first = take_snapshot(TODAY, root)
            prune_snapshots(snapshots_dir)
            second = take_snapshot(TODAY, root)
            prune_snapshots(snapshots_dir)

            remaining = find_snapshots(snapshots_dir)
            self.assertEqual(
                [p.name for p in remaining],
                ["sweep-2026-10-08.jsonl", "sweep-2026-10-15.jsonl"],
            )
            self.assertEqual(first.read_bytes(), WEEK_B.read_bytes())
            self.assertEqual(second.read_bytes(), first.read_bytes())

    def test_zero_sessions_aborts_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sweep_results.jsonl").write_text(
                '{"source": "codex", "path": "/x.jsonl", "error": "boom"}\n',
                encoding="utf-8",
            )
            with self.assertRaises(WeeklyError) as cm:
                take_snapshot(TODAY, root)
            self.assertIn("zero sessions", str(cm.exception))

    def test_missing_results_aborts_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(WeeklyError):
                take_snapshot(TODAY, Path(tmp))


class DiffTest(unittest.TestCase):
    def test_diff_growth_keyed_by_path(self):
        current = load_results(WEEK_B)
        previous = load_results(WEEK_A)
        trends, removed = diff_snapshots(current, previous)
        by_path = {t.path: t for t in trends}

        self.assertEqual(by_path["/fake/sessions/a1.jsonl"].growth_tokens, 10000)
        self.assertFalse(by_path["/fake/sessions/a1.jsonl"].is_new)
        self.assertEqual(by_path["/fake/sessions/a2.jsonl"].growth_tokens, -1000)
        self.assertTrue(by_path["/fake/sessions/a4.jsonl"].is_new)
        self.assertEqual(by_path["/fake/sessions/a4.jsonl"].growth_tokens, 8000)
        # error records are not sessions
        self.assertNotIn("/fake/sessions/broken.jsonl", by_path)
        self.assertEqual(removed, [])

    def test_diff_marks_removed_sessions(self):
        current = load_results(WEEK_B)
        previous = load_results(WEEK_A)
        _, removed = diff_snapshots(current, previous)
        self.assertEqual(removed, [])

        current.pop("/fake/sessions/a2.jsonl")
        _, removed = diff_snapshots(current, previous)
        self.assertEqual(removed, ["/fake/sessions/a2.jsonl"])

    def test_baseline_diff_has_no_growth_and_no_new_sessions(self):
        trends, removed = diff_snapshots(load_results(WEEK_B), None)
        self.assertEqual(removed, [])
        for t in trends:
            self.assertEqual(t.growth_tokens, 0)
            self.assertFalse(t.is_new)


class ScoringTest(unittest.TestCase):
    def test_top3_order_is_deterministic_with_reasons(self):
        trends, _ = diff_snapshots(load_results(WEEK_B), load_results(WEEK_A))
        outliers = score_sessions(trends)
        self.assertEqual(
            [o.trend.path for o in outliers],
            [
                "/fake/sessions/a1.jsonl",
                "/fake/sessions/a4.jsonl",
                "/fake/sessions/a3.jsonl",
            ],
        )
        # a1 and a4 tie on score; a1 wins the deterministic tie-break
        # (larger window)
        self.assertEqual(outliers[0].score, outliers[1].score)
        self.assertGreater(outliers[0].trend.total_tokens, outliers[1].trend.total_tokens)
        self.assertGreater(outliers[1].score, outliers[2].score)
        for o in outliers:
            self.assertTrue(o.reason)
            self.assertIn("window", o.reason)
        again = score_sessions(diff_snapshots(load_results(WEEK_B), load_results(WEEK_A))[0])
        self.assertEqual(
            [o.trend.path for o in again], [o.trend.path for o in outliers]
        )

    def test_reason_mentions_growth_and_signals(self):
        trends, _ = diff_snapshots(load_results(WEEK_B), load_results(WEEK_A))
        top = score_sessions(trends)[0]
        self.assertIn("grew +10,000 tokens week-over-week", top.reason)
        self.assertIn("2 bloat signal(s)", top.reason)
        self.assertIn("largest this week", top.reason)


class RunWeeklyTest(unittest.TestCase):
    def test_baseline_run_without_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = run_weekly(
                today=TODAY, root=root, run_sweep=_fake_sweep(root, WEEK_B)
            )
            self.assertTrue(manifest["baseline"])
            self.assertIsNone(manifest["previous_snapshot"])
            self.assertEqual(manifest["sessions"], 4)
            self.assertEqual(manifest["removed_sessions"], [])
            # baseline: no growth signal, but manifest + outliers still written
            self.assertTrue(
                all(o["growth_tokens"] == 0 for o in manifest["outliers"])
            )
            on_disk = json.loads((root / "reports" / "deepdive.json").read_text())
            self.assertEqual(on_disk, manifest)
            self.assertTrue(
                (root / "reports" / "snapshots" / "sweep-2026-10-15.jsonl").exists()
            )

    def test_weekly_run_diffs_against_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshots_dir = root / "reports" / "snapshots"
            snapshots_dir.mkdir(parents=True)
            (snapshots_dir / "sweep-2026-10-08.jsonl").write_text(
                WEEK_A.read_text(encoding="utf-8"), encoding="utf-8"
            )
            manifest = run_weekly(
                today=TODAY, root=root, run_sweep=_fake_sweep(root, WEEK_B)
            )
            self.assertFalse(manifest["baseline"])
            self.assertEqual(
                manifest["previous_snapshot"], "reports/snapshots/sweep-2026-10-08.jsonl"
            )
            top = manifest["outliers"][0]
            self.assertEqual(top["path"], "/fake/sessions/a1.jsonl")
            self.assertEqual(top["growth_tokens"], 10000)
            self.assertEqual(top["signal_count"], 2)

    def test_zero_sessions_in_results_aborts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def empty_sweep() -> None:
                (root / "sweep_results.jsonl").write_text("", encoding="utf-8")

            with self.assertRaises(WeeklyError):
                run_weekly(today=TODAY, root=root, run_sweep=empty_sweep)
            self.assertFalse((root / "reports" / "deepdive.json").exists())

    def test_failed_sweep_runner_aborts_before_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sweep_runner.py").write_text(
                "import sys; sys.exit(1)\n", encoding="utf-8"
            )
            with self.assertRaises(WeeklyError) as cm:
                run_weekly(today=TODAY, root=root)
            self.assertIn("exited 1", str(cm.exception))
            self.assertFalse((root / "reports" / "snapshots").exists())


if __name__ == "__main__":
    unittest.main()
