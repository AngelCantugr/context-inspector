"""Figure #6: tool ranking — horizontal bars for the top 10 tools by total
tool-result tokens, over the ``tool_ranking`` query output.

The overall ranking (source="all" rows) is drawn. The shell family —
``exec`` / ``Bash`` / ``exec_command`` / ``shell_command`` / ``shell`` /
``bash``, the same conceptual tool under different harness names — is
grouped into a single "shell (all harnesses)" bar so the ranking reads as
tool concepts, not harness naming accidents. Ungrouped per-source numbers
are preserved verbatim in the sidecar JSON, so the figure stays auditable
against the raw query output.

Color is information here: the grouped ``shell (all harnesses)`` bar is
drawn in the tool-result vermillion so the single aggregated bar stands
out, and every other bar uses the shared neutral gray
(:data:`style.NEUTRAL_COLOR`) because ranked tools carry no category
meaning.

Every bar is annotated with total tokens (compact), share of tool-result
tokens, and tokens per call — the last one exposes high-total/high-tpc
outliers (e.g. a REPL that returns whole transcripts per call) next to
high-volume/low-tpc workhorses.

The footnote is part of the figure: it carries the grouping caveat and the
estimator caveat (heuristic tokens, ±15% on typical English-heavy logs,
worse for CJK/base64), plus an optional ``data_date`` derived from the
input data by the caller — never today.
"""

from __future__ import annotations

from pathlib import Path

from . import style
from .style import plt
import matplotlib.ticker as mtick  # noqa: E402  (module-level, like style.py)

FIGURE_NAME = "tool_ranking"
TITLE = "Top tools by tool-result tokens"
GROUPED_LABEL = "shell (all harnesses)"

#: Tool names that are the same conceptual shell-execution tool under
#: different harnesses; aggregated into one bar under GROUPED_LABEL.
SHELL_FAMILY = frozenset(
    {"exec", "Bash", "bash", "exec_command", "shell_command", "shell"}
)

TOP_N = 10

DATA_NOTE = (
    "exec/Bash/exec_command/shell_command/shell/bash counted as one "
    "'shell (all harnesses)' bar; ungrouped per-source numbers are in the "
    "sidecar JSON"
)

#: Estimator caveat with the accuracy envelope called out (part of the spec).
ESTIMATOR_NOTE = (
    "token counts are heuristic estimates (±15% on typical English-heavy "
    "logs; worse for CJK/base64)"
)


def _compact(n: float) -> str:
    """Compact token/interval label: 13330296 -> '13.3M', 452371 -> '452k'."""
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.0f}k"
    return str(int(n))


def aggregate_tools(rows: list[dict], *, top_n: int = TOP_N) -> list[dict]:
    """Rank overall (source="all") tools by tokens, grouping the shell family.

    Returns up to ``top_n`` dicts of
    {tool, tokens, share, calls, tokens_per_call, grouped} sorted by tokens
    descending. Shares are recomputed against the grand total of all
    source="all" rows, so the grouped bar's share reflects the aggregation.
    Pure Python (no matplotlib) so the grouping logic is unit-testable.
    """
    tokens_by_tool: dict[str, int] = {}
    calls_by_tool: dict[str, int] = {}
    for row in rows:
        if row.get("source") != "all":
            continue
        tool = row.get("tool")
        if not tool:
            continue
        tokens = int(row.get("tokens") or 0)
        calls = int(row.get("calls") or 0)
        tokens_by_tool[tool] = tokens_by_tool.get(tool, 0) + tokens
        calls_by_tool[tool] = calls_by_tool.get(tool, 0) + calls
    if not tokens_by_tool:
        raise ValueError("no source='all' tool data to render")

    grand_total = sum(tokens_by_tool.values())
    aggregated: list[dict] = []
    grouped_tokens = sum(
        t for tool, t in tokens_by_tool.items() if tool in SHELL_FAMILY
    )
    grouped_calls = sum(
        c for tool, c in calls_by_tool.items() if tool in SHELL_FAMILY
    )
    if grouped_tokens > 0:
        aggregated.append(
            {
                "tool": GROUPED_LABEL,
                "tokens": grouped_tokens,
                "share": grouped_tokens / grand_total,
                "calls": grouped_calls,
                "tokens_per_call": grouped_tokens / grouped_calls
                if grouped_calls
                else 0.0,
                "grouped": True,
            }
        )
    for tool in sorted(tokens_by_tool, key=lambda t: (-tokens_by_tool[t], t)):
        if tool in SHELL_FAMILY:
            continue
        tokens = tokens_by_tool[tool]
        calls = calls_by_tool[tool]
        aggregated.append(
            {
                "tool": tool,
                "tokens": tokens,
                "share": tokens / grand_total,
                "calls": calls,
                "tokens_per_call": tokens / calls if calls else 0.0,
                "grouped": False,
            }
        )
    return aggregated[:top_n]


def _per_source_ungrouped(rows: list[dict]) -> dict[str, list[dict]]:
    """All per-source rows verbatim, sorted by source then tokens descending."""
    by_source: dict[str, list[dict]] = {}
    for row in rows:
        source = row.get("source")
        if not source or source == "all":
            continue
        by_source.setdefault(source, []).append(
            {
                "tool": row.get("tool"),
                "tokens": int(row.get("tokens") or 0),
                "share": float(row.get("share") or 0.0),
                "calls": int(row.get("calls") or 0),
                "tokens_per_call": float(row.get("tokens_per_call") or 0.0),
            }
        )
    for source_rows in by_source.values():
        source_rows.sort(key=lambda r: (-r["tokens"], r["tool"] or ""))
    return {s: by_source[s] for s in sorted(by_source)}


def _footnote(data_date: str | None) -> str:
    # Sentence-case the first letter only: str.capitalize() would also
    # lowercase the "CJK"/"base64" inside the parenthetical.
    parts = [
        f"Note: {DATA_NOTE}.",
        ESTIMATOR_NOTE[0].upper() + ESTIMATOR_NOTE[1:] + ".",
    ]
    if data_date:
        parts.append(f"Data as of {data_date}.")
    return " ".join(parts)


def render_tool_ranking(
    rows: list[dict],
    out_dir: Path,
    *,
    data_date: str | None = None,
    title: str = TITLE,
    top_n: int = TOP_N,
) -> Path:
    """Render the horizontal-bar ranking; return the PNG path.

    ``rows`` is the parsed ``tool_ranking`` query output (JSON list of
    {"source", "tool", "tokens", "share", "calls", "tokens_per_call"}
    dicts). Only source="all" rows drive the figure; per-source rows are
    carried into the sidecar ungrouped.
    """
    style.apply_style()
    ranked = aggregate_tools(rows, top_n=top_n)

    fig, ax = plt.subplots(figsize=(7.0, 4.5))
    fig.subplots_adjust(left=0.24, right=0.98, top=0.90, bottom=0.24)

    # Barh draws bottom-up; reverse so the top tool sits at the top.
    order = list(reversed(ranked))
    y = range(len(order))
    # The grouped shell bar gets the tool-result vermillion so the single
    # aggregated bar stands out; every other bar is the shared neutral
    # gray — ranked tools carry no category meaning.
    colors = [
        style.CATEGORY_COLORS["tool result"] if row["grouped"]
        else style.NEUTRAL_COLOR
        for row in order
    ]
    ax.barh(y, [row["tokens"] for row in order], 0.62, color=colors,
            edgecolor="white", linewidth=0.5)

    max_tokens = ranked[0]["tokens"]
    for yi, row in zip(y, order):
        label = (
            f"{_compact(row['tokens'])} · {row['share'] * 100:.1f}% · "
            f"~{_compact(row['tokens_per_call'])} tok/call"
        )
        ax.text(
            row["tokens"] + max_tokens * 0.01,
            yi,
            label,
            va="center",
            ha="left",
            fontsize=style.FONT_SIZES["segment_label"],
            color=style.TEXT_COLOR,
        )

    ax.set_yticks(list(y))
    ax.set_yticklabels([row["tool"] for row in order])
    ax.set_xlim(0, max_tokens * 1.52)
    ax.xaxis.set_major_locator(mtick.MaxNLocator(6))
    ax.xaxis.set_major_formatter(style.thousands_formatter())
    ax.set_xlabel("tool-result tokens")
    ax.set_title(title, loc="left", fontweight="bold")
    style.add_footnote(fig, _footnote(data_date))

    sidecar = {
        "figure": FIGURE_NAME,
        "title": title,
        "data_date": data_date,
        "top_n": top_n,
        "shell_family": sorted(SHELL_FAMILY),
        "grouped_label": GROUPED_LABEL,
        "tools": ranked,
        "per_source_ungrouped": _per_source_ungrouped(rows),
    }
    return style.save(fig, Path(out_dir), FIGURE_NAME, sidecar)
