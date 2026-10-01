"""Chart pack (L2): matplotlib figures over the L1 analytics query pack.

Every figure renders a PNG to ``reports/figures/<name>.png`` plus a sidecar
``<name>.json`` holding the exact data rendered, and is styled exclusively
through :mod:`context_inspector.charts.style` — no ad hoc colors or fonts.

This package stays import-light: matplotlib is an optional dependency of the
``[analytics]`` extra and is only imported via :func:`_matplotlib`, which
raises a friendly error when it is missing.
"""

from __future__ import annotations


def _matplotlib():
    """Import matplotlib lazily, with an install hint when it is missing."""
    try:
        import matplotlib
    except ImportError as exc:
        raise RuntimeError(
            "matplotlib is not installed. Install it with "
            '`pip install "context-inspector[analytics]"` '
            '(or `uv pip install -e ".[analytics]"` from a clone).'
        ) from exc
    return matplotlib
