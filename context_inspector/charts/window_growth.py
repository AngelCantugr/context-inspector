"""Figure #7: window growth — cumulative tokens per message index for a single
session log.

Unlike the other figures, this one plots a *session*, not query output: the
caller loads the log with :func:`context_inspector.adapters.load_any` and
passes the message list; this module runs :func:`context_inspector.analyzer.
analyze` (pure, deterministic) and renders the cumulative token curve.

The Y axis is LOG-scaled, and that is the point of the figure: on a linear
axis a single "whale wall" — one tool result dumping megatokens — goes
near-vertical and compresses every other message into the noise floor at the
bottom, making the rest of the session unreadable. On a log axis the whale is
one tall step and the thousands of small steps stay visible above the floor.

The figure's payload is the annotation of the top-3 largest single-message
jumps (category, plus tool name when the analyzer can attribute it). Exact
duplicate message indices (from the analyzer) get faint vertical marks — a
re-sent message is a jump that bought nothing.

Footnote caveats (both mandatory): token counts are heuristic estimates, and
cumulative content of an append-only log is NOT the instantaneous window —
compaction/summarization resets the real window, so the curve's right edge
overstates live context.

Determinism: same message list → byte-identical PNG (fixed offsets, fixed
top-3, no clock, fixed metadata via :func:`style.save`).

Sidecar choice: the per-message cumulative series is stored in full but in
parallel-array compact form ({"index", "tokens", "cumulative"} lists) — a few
thousand small integers, tens of KB, fully auditable and regenerable, unlike
a downsampled series. Jumps, category totals, totals, duplicates, and
provenance (source/session_path) are stored alongside it.
"""

from __future__ import annotations

from pathlib import Path

from ..analyzer import MessageStat, Report, analyze
from . import style
from .style import plt

FIGURE_NAME = "window_growth"
TITLE = "Context window growth over the session"

#: A tool-result message whose name is one of these carries no attribution
#: (mirrors the analyzer's own rule: bare "tool" is as good as unknown).
_UNATTRIBUTED = {"", "?", "tool"}

#: Fixed per-rank label offsets in points (deterministic, tuned for a
#: right-heavy log curve): the biggest whale goes up-right, the next down-
#: right, the third up-left — they never land on top of each other.
_LABEL_OFFSETS_POINTS = ((34, 22), (34, -30), (-96, 30))
#: Same, for points in the top half of the log range (a saturated curve)
#: where an upward label would collide with the title.
_LABEL_OFFSETS_NEAR_TOP = ((40, -35), (60, -25), (-96, -35))

_DUPLICATE_MARK_COLOR = style.CATEGORY_COLORS["harness context"]
_LINE_COLOR = style.CATEGORY_COLORS["user"]


def top_jumps(stats: list[MessageStat], n: int = 3) -> list[MessageStat]:
    """The n largest single-message jumps, best first (ties by index)."""
    return sorted(stats, key=lambda s: (-s.tokens, s.index))[:n]


def jump_label(stat: MessageStat, resolved_name: str | None = None) -> str:
    """Annotation text for one jump: size, category, tool name if attributable.

    ``resolved_name`` is the caller's best attribution (via the tool-call
    id), falling back to :attr:`MessageStat.name`; either way a bare
    "tool"/"?" means category only.
    """
    name = (resolved_name or stat.name or "").strip()
    if stat.category == "tool result" and name not in _UNATTRIBUTED:
        return f"+{stat.tokens:,}\ntool result: {name}"
    return f"+{stat.tokens:,}\n{stat.category}"


def resolve_tool_names(messages: list[dict]) -> dict[int, str]:
    """Attribute tool-result messages to tool names via tool-call ids.

    The analyzer does this internally for aggregate ``tool_tokens`` but
    :class:`MessageStat` keeps only the raw (often bare "tool") name; this
    mirrors the analyzer's id→name mapping so annotations can credit e.g.
    ``js_repl``. Pure: reads only the passed message list.
    """
    id2name: dict[str, str] = {}
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        for tc in msg.get("tool_calls") or []:
            cid = tc.get("id") or ""
            if cid:
                id2name[cid] = tc.get("function", {}).get("name") or "?"
    names: dict[int, str] = {}
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        name = id2name.get(msg.get("tool_call_id") or "")
        if name and name not in _UNATTRIBUTED:
            names[i] = name
    return names


def _footnote(data_date: str | None, session_path: str | None) -> str:
    parts = [
        "Note: cumulative content of an append-only log — compaction or "
        "summarization resets the real window, so the curve overstates the "
        "live context at any point.",
        "Token counts are heuristic estimates.",
    ]
    if session_path:
        parts.append(f"Session: {session_path}.")
    if data_date:
        parts.append(f"Data as of {data_date}.")
    return " ".join(parts)


def render_window_growth(
    messages: list[dict],
    out_dir: Path,
    *,
    source: str | None = None,
    session_path: str | None = None,
    data_date: str | None = None,
    title: str = TITLE,
) -> Path:
    """Render the cumulative-token growth curve; return the PNG path.

    ``messages`` is an OpenAI-format message list already loaded by the
    caller (typically via ``adapters.load_any``); this module loads nothing
    from disk itself. ``source`` and ``session_path`` are provenance only —
    they appear in the sidecar and footnote, never in the plotted data.
    """
    if not messages:
        raise ValueError("no messages to render")

    style.apply_style()
    report: Report = analyze(messages)
    stats = report.messages
    if not stats:
        raise ValueError("no analyzable messages to render")

    xs = [s.index for s in stats]
    # Log axis cannot show 0; cumulative only hits 0 while leading messages
    # carry no tokens, so floor the display at 1 (a 0-token step is invisible
    # anyway — the curve is flat there).
    ys = [max(s.cumulative, 1) for s in stats]

    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    fig.subplots_adjust(left=0.10, right=0.97, top=0.90, bottom=0.24)

    ax.plot(xs, ys, color=_LINE_COLOR, linewidth=1.4, zorder=3)
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(style.thousands_formatter())
    ax.set_xlabel("message index")
    ax.set_ylabel("cumulative tokens (log scale)")
    ax.set_title(title, loc="left", fontweight="bold")

    # Exact duplicates: vertical marks — a re-sent message is a jump that
    # bought no new information.
    plotted = set(xs)
    for dup in report.duplicates:
        if dup in plotted:
            ax.axvline(dup, color=_DUPLICATE_MARK_COLOR, linewidth=0.7,
                       zorder=1)

    # Payload: top-3 jumps, attributed. Marker colored by category.
    names_by_index = resolve_tool_names(messages)
    jumps = top_jumps(stats, 3)
    # Top half of the log range = saturated curve; labels must go down.
    near_top_floor = (max(ys) * min(ys)) ** 0.5
    rendered_jumps = []
    for rank, stat in enumerate(jumps):
        if stat.tokens <= 0:
            continue
        name = names_by_index.get(stat.index)
        y = max(stat.cumulative, 1)
        offsets = (_LABEL_OFFSETS_NEAR_TOP if y >= near_top_floor
                   else _LABEL_OFFSETS_POINTS)
        ax.plot([stat.index], [y], marker="o", markersize=5,
                color=style.CATEGORY_COLORS.get(stat.category, "#777777"),
                zorder=4)
        ax.annotate(
            jump_label(stat, name),
            xy=(stat.index, y),
            xytext=offsets[rank % len(offsets)],
            textcoords="offset points",
            fontsize=style.FONT_SIZES["segment_label"],
            ha="left",
            arrowprops={"arrowstyle": "-", "linewidth": 0.7,
                        "color": "#555555"},
            zorder=5,
        )
        rendered_jumps.append(
            {
                "rank": rank + 1,
                "index": stat.index,
                "tokens": stat.tokens,
                "cumulative": stat.cumulative,
                "category": stat.category,
                "name": name or stat.name or None,
            }
        )

    if report.duplicates:
        ax.plot([], [], color=_DUPLICATE_MARK_COLOR, linewidth=0.7,
                label=f"exact duplicate re-send (n={len(report.duplicates)})")
        ax.legend(loc="upper left")

    style.add_footnote(fig, _footnote(data_date, session_path))

    sidecar = {
        "figure": FIGURE_NAME,
        "title": title,
        "source": source,
        "session_path": session_path,
        "data_date": data_date,
        "y_scale": "log",
        "total_tokens": report.total_tokens,
        "category_tokens": report.category_tokens,
        "n_messages": len(stats),
        "duplicates": report.duplicates,
        "top_jumps": rendered_jumps,
        # Parallel-array compact series — see module docstring.
        "series": {
            "index": xs,
            "tokens": [s.tokens for s in stats],
            "cumulative": [s.cumulative for s in stats],
        },
    }
    return style.save(fig, Path(out_dir), FIGURE_NAME, sidecar)
