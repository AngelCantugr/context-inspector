"""Analytics layer (L1): DuckDB over sweep results.

Optional — requires the ``analytics`` extra (``pip install ".[analytics]"``).
duckdb is imported lazily inside ``loader.load_results`` so the base CLI
stays stdlib-only.
"""

from __future__ import annotations

from .loader import load_results

__all__ = ["load_results"]
