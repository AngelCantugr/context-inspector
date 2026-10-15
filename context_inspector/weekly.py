"""Weekly context-hygiene orchestrator: snapshot, diff, outlier manifest.

Deterministic first stages of the weekly pipeline (design:
docs/plans/2026-10-14-weekly-context-hygiene-design.md). Regenerates the
sweep results, snapshots them under reports/snapshots/, diffs this week's
snapshot against the previous one keyed by session path, and writes the
top-N outlier manifest (reports/deepdive.json) that the later interpretation
stage consumes. Stdlib only; CLI wiring lives in a later issue.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

TOP_N = 3
RESULTS = Path("sweep_results.jsonl")
SNAPSHOTS_DIR = Path("reports") / "snapshots"
MANIFEST = Path("reports") / "deepdive.json"
SNAPSHOT_RE = re.compile(r"^sweep-(\d{4}-\d{2}-\d{2})\.jsonl$")


class WeeklyError(RuntimeError):
    """Fatal weekly-pipeline failure — abort loudly, never write a half result."""


@dataclass
class SessionTrend:
    """One session's week-over-week trend entry (a diff row)."""

    path: str
    source: str
    total_tokens: int
    wasted_tokens: int
    signals: list[str]
    growth_tokens: int  # 0 on the baseline run; full window when new
    is_new: bool = False
    removed: bool = False


@dataclass
class ScoredSession:
    """A current-week session with its normalized signal scores."""

    trend: SessionTrend
    window_score: float
    growth_score: float
    wasted_score: float
    signal_score: float
    reason: str = ""
    score: float = field(init=False)

    def __post_init__(self) -> None:
        self.score = (
            self.window_score
            + self.growth_score
            + self.wasted_score
            + self.signal_score
        )

    @property
    def wasted_ratio(self) -> float:
        if self.trend.total_tokens <= 0:
            return 0.0
        return self.trend.wasted_tokens / self.trend.total_tokens

    def to_dict(self) -> dict:
        return {
            "path": self.trend.path,
            "source": self.trend.source,
            "total_tokens": self.trend.total_tokens,
            "growth_tokens": self.trend.growth_tokens,
            "wasted_ratio": round(self.wasted_ratio, 4),
            "signal_count": len(self.trend.signals),
            "score": round(self.score, 4),
            "reason": self.reason,
        }


def _run_sweep_script(root: Path) -> None:
    """Regenerate sweep_results.jsonl via the resumable sweep_runner.py."""
    script = root / "sweep_runner.py"
    if not script.exists():
        raise WeeklyError(f"sweep runner not found: {script}")
    proc = subprocess.run(
        [sys.executable, "sweep_runner.py"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise WeeklyError(
            f"sweep_runner.py exited {proc.returncode}: "
            f"{proc.stderr.strip()[:400] or proc.stdout.strip()[:400]}"
        )


def load_results(path: Path) -> dict[str, dict]:
    """Parse a sweep-results JSONL file into {path: record}.

    Lines that are blank, unparseable, or error records (no total_tokens)
    are skipped.
    """
    records: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict) or "path" not in rec or "total_tokens" not in rec:
            continue
        records[rec["path"]] = rec
    return records


def find_snapshots(snapshots_dir: Path) -> list[Path]:
    """Dated snapshot files, oldest first."""
    dated = []
    for p in snapshots_dir.glob("sweep-*.jsonl"):
        m = SNAPSHOT_RE.match(p.name)
        if m:
            dated.append((date.fromisoformat(m.group(1)), p))
    return [p for _, p in sorted(dated)]


def take_snapshot(today: date, root: Path) -> Path:
    """Copy sweep_results.jsonl to reports/snapshots/sweep-<today>.jsonl.

    Aborts loudly when the results file is missing or contains zero valid
    session records. A same-date re-run overwrites the same file (idempotent
    within a week); older dated snapshots are never touched here — pruning
    happens in run_weekly once the new snapshot exists. Only the two most
    recent snapshots are kept.
    """
    results = root / RESULTS
    if not results.exists():
        raise WeeklyError(f"{RESULTS} not found — the sweep produced no results")
    records = load_results(results)
    if not records:
        raise WeeklyError(f"zero sessions found in {RESULTS} — aborting")
    snapshots_dir = root / SNAPSHOTS_DIR
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    snapshot = snapshots_dir / f"sweep-{today.isoformat()}.jsonl"
    shutil.copyfile(results, snapshot)
    return snapshot


def prune_snapshots(snapshots_dir: Path, keep: int = 2) -> None:
    """Keep only the `keep` most recent dated snapshots."""
    for old in find_snapshots(snapshots_dir)[:-keep] if keep else find_snapshots(snapshots_dir):
        old.unlink()


def diff_snapshots(
    current: dict[str, dict], previous: dict[str, dict] | None
) -> tuple[list[SessionTrend], list[str]]:
    """Diff this week's records against last week's, keyed by session path.

    Shared sessions get growth = this_week - last_week; sessions only in the
    current snapshot are new (growth = full window — they grew from zero);
    sessions only in the previous snapshot are returned as removed paths and
    never scored. On the baseline run (previous=None) there is no comparison
    at all: growth is 0 and no session is flagged new.
    """
    trends: list[SessionTrend] = []
    for path, rec in current.items():
        total = int(rec.get("total_tokens") or 0)
        if previous is None:
            growth, is_new = 0, False
        elif path in previous:
            growth = total - int(previous[path].get("total_tokens") or 0)
            is_new = False
        else:
            growth, is_new = total, True
        trends.append(
            SessionTrend(
                path=path,
                source=str(rec.get("source", "")),
                total_tokens=total,
                wasted_tokens=int(rec.get("wasted_tokens") or 0),
                signals=list(rec.get("signals") or []),
                growth_tokens=growth,
                is_new=is_new,
            )
        )
    removed = [p for p in previous if p not in current] if previous else []
    return trends, removed


def _norm(value: float, max_value: float) -> float:
    return value / max_value if max_value > 0 else 0.0


def _reason(trend: SessionTrend, window_is_max: bool, max_window: int) -> str:
    """Human-readable per-session reason the scorer selected it."""
    parts = [f"window {trend.total_tokens:,} tokens"]
    if window_is_max and max_window > 0:
        parts[0] += " — largest this week"
    if trend.is_new:
        parts.append("new session this week")
    elif trend.growth_tokens > 0:
        parts.append(f"grew +{trend.growth_tokens:,} tokens week-over-week")
    elif trend.growth_tokens < 0:
        parts.append(f"shrank {-trend.growth_tokens:,} tokens week-over-week")
    wasted = (
        trend.wasted_tokens / trend.total_tokens if trend.total_tokens > 0 else 0.0
    )
    if wasted > 0:
        parts.append(f"wasted-token ratio {wasted:.0%}")
    if trend.signals:
        parts.append(f"{len(trend.signals)} bloat signal(s)")
    return "; ".join(parts)


def score_sessions(trends: list[SessionTrend], top_n: int = TOP_N) -> list[ScoredSession]:
    """Score each session on the four signals and return the top_n outliers.

    Each signal is normalized to [0, 1] against the week's maximum; the
    composite score is their sum. Ranking is deterministic: score desc, then
    total_tokens desc, then path asc.
    """
    max_window = max((t.total_tokens for t in trends), default=0)
    max_growth = max((max(t.growth_tokens, 0) for t in trends), default=0)
    max_wasted = max(
        (
            t.wasted_tokens / t.total_tokens if t.total_tokens > 0 else 0.0
            for t in trends
        ),
        default=0.0,
    )
    max_signals = max((len(t.signals) for t in trends), default=0)

    scored = [
        ScoredSession(
            trend=t,
            window_score=_norm(t.total_tokens, max_window),
            growth_score=_norm(max(t.growth_tokens, 0), max_growth),
            wasted_score=_norm(
                t.wasted_tokens / t.total_tokens if t.total_tokens > 0 else 0.0,
                max_wasted,
            ),
            signal_score=_norm(len(t.signals), max_signals),
        )
        for t in trends
    ]
    for s in scored:
        s.reason = _reason(s.trend, s.trend.total_tokens == max_window, max_window)
    scored.sort(key=lambda s: (-s.score, -s.trend.total_tokens, s.trend.path))
    return scored[:top_n]


def run_weekly(
    *,
    today: date | None = None,
    root: Path = Path("."),
    run_sweep=None,
    top_n: int = TOP_N,
) -> dict:
    """Run the deterministic weekly stages and return the manifest dict.

    run_sweep is injectable for tests; by default it runs sweep_runner.py
    as a subprocess in `root`.
    """
    today = today or date.today()
    root = Path(root)
    (run_sweep or (lambda: _run_sweep_script(root)))()

    snapshot = take_snapshot(today, root)
    prune_snapshots(root / SNAPSHOTS_DIR)

    snapshots = find_snapshots(root / SNAPSHOTS_DIR)
    previous = next(
        (p for p in reversed(snapshots) if p.name != snapshot.name), None
    )

    current_records = load_results(snapshot)
    previous_records = load_results(previous) if previous else None
    trends, removed = diff_snapshots(current_records, previous_records)
    if not trends:
        raise WeeklyError("zero sessions found in this week's snapshot — aborting")

    outliers = score_sessions(trends, top_n=top_n)
    manifest = {
        "generated": today.isoformat(),
        "snapshot": snapshot.relative_to(root).as_posix(),
        "previous_snapshot": (
            previous.relative_to(root).as_posix() if previous else None
        ),
        "baseline": previous is None,
        "sessions": len(trends),
        "removed_sessions": removed,
        "outliers": [s.to_dict() for s in outliers],
    }
    manifest_path = root / MANIFEST
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest
