"""Charts runner (#9): orchestrate the L2 figure pack over the L1 query pack.

One ``charts`` CLI invocation renders any subset of the four figures with a
single shared ``data_date``, derived deterministically from the data (the
max month in the ``monthly_trend`` query output — never ``today``; omitted
when not derivable, rather than breaking byte-identity).

The three query-driven figures never re-implement SQL: with ``--sweep`` the
runner re-runs the needed L1 queries into a temp dir (the SQL stays the
source of truth); without it the runner reads existing
``reports/queries/<name>.json`` files, typically produced by the ``query``
subcommand. ``window_growth`` is different by design — it plots one session,
so the runner loads the log with :func:`context_inspector.adapters.load_any`
and calls the figure module in-process.

Import-light like the rest of the package: the figure modules (and through
them matplotlib) are imported lazily per render, so unknown-figure
validation and error handling stay stdlib-only here.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

FIGURE_NAMES = ("category_composition", "tool_ranking", "window_growth",
                "monthly_trend")

#: L1 queries each query-driven figure needs, in runner load order.
QUERY_DEPS: dict[str, tuple[str, ...]] = {
    "category_composition": ("category_shares",),
    "tool_ranking": ("tool_ranking",),
    "monthly_trend": ("monthly_trend", "monthly_trend_by_source"),
}

DEFAULT_OUT_DIR = Path("reports/figures")
DEFAULT_QUERIES_DIR = Path("reports/queries")


def _load_query_rows(queries_dir: Path, name: str) -> list[dict]:
    path = queries_dir / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"query output {path} not found — rerun with --sweep PATH to "
            f"regenerate it from a sweep, or run `python -m context_inspector "
            f"query <sweep_results.jsonl> --query {name}` first"
        )
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"query output {path} is not a JSON list")
    return rows


def _run_queries(sweep: str, names: list[str]) -> dict[str, list[dict]]:
    """Re-run the named L1 queries from ``sweep`` into a temp dir; parse rows."""
    from ..analytics import load_results
    from ..analytics.runner import run_queries

    con = load_results(sweep)  # raises FileNotFoundError / RuntimeError
    with tempfile.TemporaryDirectory() as tmp:
        run_queries(con, names, Path(tmp))
        return {n: json.loads((Path(tmp) / f"{n}.json").read_text(encoding="utf-8"))
                for n in names}


def _derive_data_date(query_rows: dict[str, list[dict]]) -> str | None:
    """Max month ('YYYY-MM') across the monthly_trend rows, else None."""
    months = [str(r.get("month"))[:7] for r in query_rows.get("monthly_trend", [])
              if r.get("month") is not None]
    return max(months) if months else None


def _render(figure: str, query_rows: dict[str, list[dict]], messages, *,
            source, session, data_date, out_dir) -> Path:
    if figure == "category_composition":
        from .category_composition import render_category_composition
        return render_category_composition(
            query_rows["category_shares"], out_dir, data_date=data_date
        )
    if figure == "tool_ranking":
        from .tool_ranking import render_tool_ranking
        return render_tool_ranking(
            query_rows["tool_ranking"], out_dir, data_date=data_date
        )
    if figure == "monthly_trend":
        from .monthly_trend import render_monthly_trend
        return render_monthly_trend(
            query_rows["monthly_trend"], query_rows["monthly_trend_by_source"],
            out_dir, data_date=data_date,
        )
    if figure == "window_growth":
        from .window_growth import render_window_growth
        return render_window_growth(
            messages, out_dir, source=source, session_path=session,
            data_date=data_date,
        )
    raise ValueError(f"unknown figure {figure!r}")  # unreachable: pre-validated


def run_charts(*, figures: list[str] | None = None, sweep: str | None = None,
               session: str | None = None, source: str = "auto",
               out_dir=DEFAULT_OUT_DIR,
               queries_dir=DEFAULT_QUERIES_DIR) -> list[tuple[str, Path]]:
    """Render the requested figures; return [(figure, png_path), ...].

    Raises FileNotFoundError / ValueError / RuntimeError with a specific
    message on every loud-failure path; the CLI prints them as
    ``error: ...`` and exits 1 (unknown figure names are pre-validated by
    the caller, which exits 2 like the ``query`` subcommand).

    ``window_growth`` needs ``--session``; when it arrives only via the
    default figure set (not named explicitly in ``figures``) it is skipped
    with a stderr note so the remaining figures still render, and the run
    succeeds. Naming it explicitly without ``--session`` stays a loud
    error.
    """
    explicitly_named = figures is not None
    requested = figures or list(FIGURE_NAMES)
    unknown = [f for f in requested if f not in FIGURE_NAMES]
    if unknown:
        raise ValueError(f"unknown figure {unknown[0]!r}")

    if "window_growth" in requested and not session:
        if explicitly_named:
            raise ValueError("figure 'window_growth' requires --session <log>")
        requested = [f for f in requested if f != "window_growth"]
        print("skipping window_growth (requires --session <log>)",
              file=sys.stderr)

    if "window_growth" in requested:
        session_path = Path(session)
        if not session_path.exists():
            raise FileNotFoundError(f"session log not found: {session}")

    query_figures = [f for f in requested if f in QUERY_DEPS]
    needed = sorted({q for f in query_figures for q in QUERY_DEPS[f]})
    if sweep:
        query_rows = _run_queries(sweep, needed)
    else:
        query_rows = {n: _load_query_rows(Path(queries_dir), n)
                      for n in needed}
    data_date = _derive_data_date(query_rows)

    messages = None
    detected_source = None
    if "window_growth" in requested:
        from ..adapters import load_any
        messages, detected_source = load_any(
            session, None if source == "auto" else source
        )

    out = Path(out_dir)
    rendered = []
    for figure in requested:
        png = _render(figure, query_rows, messages, source=detected_source,
                      session=session, data_date=data_date, out_dir=out)
        rendered.append((figure, png))
    return rendered
