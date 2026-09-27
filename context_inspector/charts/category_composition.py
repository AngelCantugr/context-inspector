"""Figure #5: category composition per harness — one vertical stacked bar
per source over the ``category_shares`` query output.

Segments follow the fixed :data:`style.CATEGORY_ORDER` (never input order);
a source with no data is omitted, and categories a source lacks simply
render as absent segments. Shares are token-weighted across sessions.

The footnote is part of the figure: it carries the visibility caveat that
keeps the cross-harness comparison honest (Codex exposes system +
harness context; Claude Code only via prompt_snapshot in ~26% of logs;
Kimi Code hash-only) plus the estimator caveat, and an optional
``data_date`` (derived from the input data by the caller — never today).
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from . import style
from .style import plt

FIGURE_NAME = "category_composition"
TITLE = "Category composition per harness"

#: Required data note, rendered in the footnote so the figure cannot be
#: misread as an apples-to-apples comparison of system-prompt share.
DATA_NOTE = (
    "shares are token-weighted across sessions; system/harness-context "
    "visibility differs per harness (Codex exposes it; Claude Code only via "
    "prompt_snapshot, ~26% of logs; Kimi Code hash-only); Codex developer-role "
    "messages are counted as their own category"
)

LABEL_THRESHOLD = 0.05  # label segments >= 5% of the bar


def _sorted_categories(present: set[str]) -> list[str]:
    """Fixed category order first; unknown categories alphabetically after."""
    known = [c for c in style.CATEGORY_ORDER if c in present]
    return known + sorted(present - set(style.CATEGORY_ORDER))


def _sorted_sources(present: set[str]) -> list[str]:
    known = [s for s in style.SOURCE_ORDER if s in present]
    return known + sorted(present - set(style.SOURCE_ORDER))


def _label_color(hex_color: str) -> str:
    """White text on dark segments, dark text on light ones."""
    rgb = [
        int(hex_color.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4)
    ]
    luminance = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    return "white" if luminance < 0.5 else style.TEXT_COLOR


def _footnote(data_date: str | None) -> str:
    parts = [f"Note: {DATA_NOTE}.", f"Token counts are heuristic estimates."]
    if data_date:
        parts.append(f"Data as of {data_date}.")
    return " ".join(parts)


def render_category_composition(
    rows: list[dict],
    out_dir: Path,
    *,
    data_date: str | None = None,
    title: str = TITLE,
) -> Path:
    """Render the stacked-bar figure; return the PNG path.

    ``rows`` is the parsed ``category_shares`` query output (JSON list of
    {"source", "category", "tokens", "share"} dicts). The source="all"
    grand-total row set is ignored here.
    """
    style.apply_style()

    per_source: dict[str, dict[str, int]] = defaultdict(dict)
    for row in rows:
        source = row.get("source")
        category = row.get("category")
        if source in (None, "all") or not category:
            continue
        per_source[source][category] = per_source[source].get(category, 0) + int(
            row.get("tokens") or 0
        )
    # Omit sources with no data.
    per_source = {s: c for s, c in per_source.items() if sum(c.values()) > 0}
    if not per_source:
        raise ValueError("no per-source category data to render")

    sources = _sorted_sources(set(per_source))
    categories = _sorted_categories(
        {c for cats in per_source.values() for c in cats}
    )

    fig, ax = plt.subplots(figsize=(7.0, 4.5))
    # Room for the outside-right legend: six category entries live between
    # right=0.72 and the figure edge, clear of the bars and the footnote.
    fig.subplots_adjust(left=0.10, right=0.72, top=0.90, bottom=0.16)

    width = 0.55
    rendered: list[dict] = []
    for x, source in enumerate(sources):
        tokens_by_cat = per_source[source]
        total = sum(tokens_by_cat.values())
        bottom = 0.0
        cats_rendered = []
        for category in categories:
            tokens = tokens_by_cat.get(category, 0)
            share = tokens / total if total else 0.0
            color = style.CATEGORY_COLORS.get(category, style.NEUTRAL_COLOR)
            ax.bar(x, share, width, bottom=bottom, color=color, edgecolor="white",
                   linewidth=0.5)
            if share >= LABEL_THRESHOLD:
                ax.text(
                    x,
                    bottom + share / 2,
                    f"{share * 100:.0f}%",
                    ha="center",
                    va="center",
                    fontsize=style.FONT_SIZES["segment_label"],
                    color=_label_color(color),
                )
            bottom += share
            cats_rendered.append(
                {"category": category, "tokens": tokens, "share": share}
            )
        rendered.append({"source": source, "total_tokens": total,
                         "categories": cats_rendered})

    ax.set_xticks(range(len(sources)))
    ax.set_xticklabels(sources)
    ax.set_ylim(0, 1)
    ax.yaxis.set_major_formatter(style.percent_formatter())
    ax.set_ylabel("share of tokens")
    ax.set_title(title, loc="left", fontweight="bold")
    handles = [
        plt.Rectangle(
            (0, 0), 1, 1,
            color=style.CATEGORY_COLORS.get(category, style.NEUTRAL_COLOR),
        )
        for category in categories
    ]
    ax.legend(handles, categories, loc="center left", bbox_to_anchor=(1.01, 0.5))
    style.add_footnote(fig, _footnote(data_date))

    sidecar = {
        "figure": FIGURE_NAME,
        "title": title,
        "data_date": data_date,
        "category_order": categories,
        "sources": rendered,
    }
    return style.save(fig, Path(out_dir), FIGURE_NAME, sidecar)
