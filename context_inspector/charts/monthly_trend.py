"""Figure #8: monthly trend — two panels over calendar month.

Top panel: total tokens per month (bars) with sessions per month (line on a
secondary Y axis), from the ``monthly_trend`` query output. Bottom panel:
median non-empty window per month per source (lines, one color per
:data:`style.SOURCE_COLORS`), from the ``monthly_trend_by_source`` query
output.

Gap policy (bottom panel): a source draws a point only for months where it
has at least ``min_sessions`` (default 3) non-empty sessions — a median over
one or two sessions is a spiky line, not a trend. Months below the
threshold are GAPS in the line, never zero-filled: zero-fill would lie
about the difference between "no data" (the source had too few sessions to
estimate a median) and "window of zero tokens" (impossible here anyway —
the query already excludes zero-token sessions). A source with sparse
months therefore renders as a dashed-looking line with honest holes, and a
source that never reaches the threshold renders nothing at all.

Both panels come from a single read of each query output: the runner runs
the SQL and passes the parsed JSON rows in; this module never touches
duckdb and never re-queries.

The footnote is part of the figure: it carries the median-over-non-empty-
windows caveat plus the estimator caveat, and an optional ``data_date``
(derived from the input data by the caller — never today).
"""

from __future__ import annotations

from pathlib import Path

from . import style
from .style import plt

FIGURE_NAME = "monthly_trend"
TITLE = "Monthly trend"

#: Required data note, rendered in the footnote so the bottom panel cannot
#: be misread as complete coverage.
DATA_NOTE = (
    "bottom panel: median over non-empty windows only, per source, drawn "
    "only for months with >=3 sessions (fewer are gaps, not zeros)"
)

DEFAULT_MIN_SESSIONS = 3


def _month_key(month) -> str:
    """Normalize a month value (date or 'YYYY-MM-DD' string) to 'YYYY-MM'."""
    return str(month)[:7]


def _prep_overview(monthly_rows: list[dict]) -> list[dict]:
    """Sort the monthly_trend rows by month; each row keeps its raw values."""
    rows = [
        {
            "month": _month_key(r["month"]),
            "sessions": int(r.get("sessions") or 0),
            "total_tokens": int(r.get("total_tokens") or 0),
            "median_window": r.get("median_window"),
        }
        for r in monthly_rows
        if r.get("month") is not None
    ]
    rows.sort(key=lambda r: r["month"])
    return rows


def _prep_by_source(
    by_source_rows: list[dict], months: list[str], min_sessions: int
) -> dict[str, list[dict | None]]:
    """Map source -> one slot per shared-axis month (None = gap, never zero).

    A slot is a dict {"month", "median_window", "sessions"} when the source
    has >= min_sessions sessions that month, else None. A month absent from
    the by-source output is indistinguishable from "no qualifying
    sessions" — both are gaps.
    """
    per_source: dict[str, dict[str, dict]] = {}
    for row in by_source_rows:
        source = row.get("source")
        month = row.get("month")
        if not source or month is None:
            continue
        per_source.setdefault(source, {})[_month_key(month)] = {
            "month": _month_key(month),
            "sessions": int(row.get("sessions") or 0),
            "median_window": row.get("median_window"),
        }
    return {
        source: [
            slot if slot is not None and slot["sessions"] >= min_sessions else None
            for slot in (per_month.get(m) for m in months)
        ]
        for source, per_month in per_source.items()
    }


def _sorted_sources(present: set[str]) -> list[str]:
    known = [s for s in style.SOURCE_ORDER if s in present]
    return known + sorted(present - set(style.SOURCE_ORDER))


def _footnote(data_date: str | None) -> str:
    parts = [f"Note: {DATA_NOTE}.", "Token counts are heuristic estimates."]
    if data_date:
        parts.append(f"Data as of {data_date}.")
    return " ".join(parts)


def render_monthly_trend(
    monthly_rows: list[dict],
    by_source_rows: list[dict],
    out_dir: Path,
    *,
    data_date: str | None = None,
    title: str = TITLE,
    min_sessions: int = DEFAULT_MIN_SESSIONS,
) -> Path:
    """Render the two-panel monthly-trend figure; return the PNG path.

    ``monthly_rows`` is the parsed ``monthly_trend`` query output (JSON
    list of {"month", "sessions", "total_tokens", "median_window"} dicts)
    and ``by_source_rows`` the parsed ``monthly_trend_by_source`` output
    (same columns plus "source"). Each input is read exactly once here; the
    runner re-runs the SQL, this module never does.
    """
    style.apply_style()

    overview = _prep_overview(monthly_rows)
    if not overview:
        raise ValueError("no monthly data to render")
    months = [r["month"] for r in overview]
    by_source = _prep_by_source(by_source_rows, months, min_sessions)

    fig, (ax_top, ax_bottom) = plt.subplots(
        2, 1, figsize=(7.0, 6.5), sharex=True
    )
    # Left margin must swallow 9pt thousands-formatted tick labels
    # ("70,000,000" is ~108px at 150 DPI) plus the y-label; at left=0.13 the
    # auto label positioner pushed the y-labels off-figure.
    fig.subplots_adjust(left=0.15, right=0.89, top=0.93, bottom=0.18)

    # Top panel: tokens (bars, left axis) + sessions (line, right axis).
    x = range(len(months))
    ax_top.bar(x, [r["total_tokens"] for r in overview], width=0.6,
               color=style.OVERVIEW_BAR_COLOR, edgecolor="white",
               linewidth=0.5, label="total tokens")
    ax_top.yaxis.set_major_formatter(style.thousands_formatter())
    ax_top.set_ylabel("total tokens")
    ax_sessions = ax_top.twinx()
    ax_sessions.plot(x, [r["sessions"] for r in overview],
                     color=style.OVERVIEW_LINE_COLOR,
                     marker="o", linewidth=1.5, label="sessions")
    ax_sessions.set_ylabel("sessions")
    ax_sessions.spines["top"].set_visible(False)
    ax_top.set_title(title, loc="left", fontweight="bold")
    handles = [
        plt.Rectangle((0, 0), 1, 1, color=style.OVERVIEW_BAR_COLOR),
        plt.Line2D([], [], color=style.OVERVIEW_LINE_COLOR, marker="o",
                   linewidth=1.5),
    ]
    ax_top.legend(handles, ["total tokens", "sessions"], loc="upper left")

    # Bottom panel: median window per source; sparse months stay gaps.
    ax_bottom.set_title(
        f"median non-empty window per source "
        f"(months with <{min_sessions} sessions omitted)",
        loc="left", fontsize=style.FONT_SIZES["axes_label"],
    )
    rendered_sources = []
    for source in _sorted_sources(set(by_source)):
        slots = by_source[source]
        color = style.SOURCE_COLORS.get(source, style.NEUTRAL_COLOR)
        xs = [i for i, slot in enumerate(slots) if slot is not None]
        ys = [slots[i]["median_window"] for i in xs]
        if not xs:
            continue
        ax_bottom.plot(xs, ys, color=color, marker="o", linewidth=1.5,
                       label=source)
        rendered_sources.append({
            "source": source,
            "points": [
                {"month": s["month"], "median_window": s["median_window"],
                 "sessions": s["sessions"]}
                for s in slots if s is not None
            ],
        })
    ax_bottom.yaxis.set_major_formatter(style.thousands_formatter())
    ax_bottom.set_ylabel("median window (tokens)")
    if rendered_sources:
        ax_bottom.legend(loc="upper left")

    ax_bottom.set_xticks(list(x))
    ax_bottom.set_xticklabels(months, rotation=45, ha="right")
    ax_bottom.set_xlabel("month")
    style.add_footnote(fig, _footnote(data_date))

    sidecar = {
        "figure": FIGURE_NAME,
        "title": title,
        "data_date": data_date,
        "min_sessions": min_sessions,
        "months": months,
        "overview": overview,
        "sources": rendered_sources,
    }
    return style.save(fig, Path(out_dir), FIGURE_NAME, sidecar)
