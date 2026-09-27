"""Chart style foundation (#4): the only place colors, fonts, and figure
conventions are defined.

Every chart module calls :func:`apply_style` before drawing and renders its
title plus an :func:`add_footnote` source/caveat line — every blog figure
carries its estimator caveat. Figures are deterministic: no time-based
values, no unseeded randomness, categorical axes always sorted by value or
fixed category order (never input order), and PNGs are saved with explicit
metadata so regeneration from the same input is byte-identical.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import _matplotlib

_mpl = _matplotlib()
import matplotlib.pyplot as plt  # noqa: E402  (module-level, via the lazy helper)
import matplotlib.ticker as mtick  # noqa: E402

DPI = 150  # web figures

# Fixed semantic order for stacked category figures — identical segment
# order across bars, never input order. Unknown categories seen in data
# (e.g. "tool call args") render after these, alphabetically. "developer"
# (OpenAI's developer-role messages, Codex only) sits between the harness-
# injected tiers and the conversation proper.
CATEGORY_ORDER = (
    "system", "harness context", "developer", "user", "assistant",
    "tool result",
)

# One color per category. Tool result is visually dominant by design and
# gets the strong vermillion accent; system/harness context are muted
# grays/slates; user/assistant are mid-tone Okabe-Ito blue/green; developer
# is Okabe-Ito reddish purple (#CC79A7) — distinct from every other segment.
CATEGORY_COLORS = {
    "system": "#7F8C9B",
    "harness context": "#B8C2CC",
    "developer": "#CC79A7",
    "user": "#0072B2",
    "assistant": "#009E73",
    "tool result": "#D55E00",
}

# One accent per source, for figures that compare harnesses (#6–#8).
SOURCE_COLORS = {
    "claude-code": "#E69F00",
    "codex": "#0072B2",
    "kimi-code": "#CC79A7",
}
SOURCE_ORDER = ("claude-code", "codex", "kimi-code")

# Neutral gray for elements that carry no category/source meaning: the
# fallback color for unknown data keys, and (deliberately, in tool_ranking)
# every ranked bar that is not the grouped shell bar — the single shared
# neutral keeps "other/unknown" visually identical across figures.
NEUTRAL_COLOR = "#777777"

# Neutral ink for annotation text and connector arrows — dark enough to
# read on white, light enough to stay visually behind the data.
TEXT_COLOR = "#222222"
MUTED_TEXT_COLOR = "#555555"

# Overview-panel chrome for the monthly trend (#8): volume bars and the
# sessions overlay line. Same values as the system / tool-result category
# colors, but named separately so the trend figure doesn't borrow category
# semantics for panel chrome.
OVERVIEW_BAR_COLOR = "#7F8C9B"
OVERVIEW_LINE_COLOR = "#D55E00"

FONT_FAMILY = "DejaVu Sans"  # matplotlib default: deterministic everywhere
FONT_SIZES = {
    "title": 13,
    "axes_label": 10,
    "tick": 9,
    "legend": 9,
    "segment_label": 8,
    "footnote": 7.5,
}

ESTIMATOR_CAVEAT = "token counts are heuristic estimates"

# Fixed metadata written into every PNG so two runs over the same input
# are byte-identical (matplotlib otherwise embeds its version string).
PNG_METADATA = {"Software": "context-inspector"}


def apply_style() -> None:
    """Pin the shared rcParams every figure is drawn with."""
    plt.rcParams.update(
        {
            "font.family": FONT_FAMILY,
            "font.size": FONT_SIZES["tick"],
            "axes.titlesize": FONT_SIZES["title"],
            "axes.labelsize": FONT_SIZES["axes_label"],
            "xtick.labelsize": FONT_SIZES["tick"],
            "ytick.labelsize": FONT_SIZES["tick"],
            "legend.fontsize": FONT_SIZES["legend"],
            "figure.dpi": DPI,
            "savefig.dpi": DPI,
            "axes.grid": True,
            "axes.axisbelow": True,
            "grid.color": "#D9D9D9",
            "grid.linestyle": "--",
            "grid.linewidth": 0.6,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "legend.frameon": False,
        }
    )


def thousands_formatter() -> mtick.FuncFormatter:
    """Axis labels like 12,500 (for token-count axes)."""
    return mtick.FuncFormatter(lambda v, _: f"{int(v):,}")


def percent_formatter() -> mtick.PercentFormatter:
    """Axis labels like 40% (decimals in, e.g. 0.40 -> 40%)."""
    return mtick.PercentFormatter(xmax=1.0, decimals=0)


def _wrap_text(text: str, width: int) -> list[str]:
    """Deterministic fixed-width wrap.

    Greedy on spaces. A token longer than ``width`` (typically a session
    path in a footnote) is split at "/" separators near the fill edge so
    paths break at slashes, never mid-token; a separator-free piece still
    longer than ``width`` is truncated in the middle with an ellipsis.
    """
    lines: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for token in paragraph.split(" "):
            if len(token) > width:
                keep = width - 1  # room for the ellipsis
                head = (keep + 1) // 2
                token = token[:head] + "…" + token[len(token) - (keep - head):]
            while line and len(line) + 1 + len(token) > width:
                # Fill what fits, breaking the token at a path separator
                # if one lands inside the remaining room.
                room = width - len(line) - 1
                cut = token.rfind("/", 0, room + 1)
                if cut > 0:
                    line = f"{line} {token[:cut + 1]}"
                    token = token[cut + 1:]
                lines.append(line)
                line = ""
            line = f"{line} {token}" if line else token
        lines.append(line)
    return lines


def add_footnote(fig, text: str, *, wrap: int = 110) -> None:
    """Small source/caveat line at the figure bottom (every figure carries one).

    Long notes wrap at ``wrap`` characters so they never run off the
    figure edge; wrapping is fixed-width, hence deterministic.
    """
    fig.text(
        0.01,
        0.01,
        "\n".join(_wrap_text(text, wrap)),
        fontsize=FONT_SIZES["footnote"],
        color=MUTED_TEXT_COLOR,
        ha="left",
        va="bottom",
        linespacing=1.4,
    )


def save(fig, out_dir: Path, name: str, sidecar: dict) -> Path:
    """Write <name>.png + <name>.json sidecar; return the PNG path.

    The sidecar holds exactly the data rendered, so a figure can be
    audited and regenerated bit-for-bit from the pair.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / f"{name}.png"
    fig.savefig(png, metadata=PNG_METADATA)
    (out_dir / f"{name}.json").write_text(
        json.dumps(sidecar, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return png
