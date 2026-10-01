"""Figure rendering tests (#5): category composition stacked bars."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from context_inspector.charts.category_composition import render_category_composition

mpl_available = importlib.util.find_spec("matplotlib") is not None

# Mirrors the shape of the category_shares query output, including the
# source="all" grand-total rows (ignored) and a source missing categories
# (kimi-code has no system/harness context data).
ROWS = [
    {"source": "all", "category": "tool result", "tokens": 4030, "share": 0.4287},
    {"source": "all", "category": "user", "tokens": 2170, "share": 0.2309},
    {"source": "claude-code", "category": "tool result", "tokens": 1700, "share": 0.4146},
    {"source": "claude-code", "category": "user", "tokens": 900, "share": 0.2195},
    {"source": "claude-code", "category": "system", "tokens": 800, "share": 0.1951},
    {"source": "claude-code", "category": "assistant", "tokens": 700, "share": 0.1707},
    {"source": "codex", "category": "tool result", "tokens": 1880, "share": 0.4585},
    {"source": "codex", "category": "assistant", "tokens": 1000, "share": 0.2439},
    {"source": "codex", "category": "user", "tokens": 820, "share": 0.2},
    {"source": "codex", "category": "system", "tokens": 400, "share": 0.0976},
    {"source": "codex", "category": "harness context", "tokens": 150, "share": 0.0366},
    {"source": "codex", "category": "developer", "tokens": 300, "share": 0.0732},
    {"source": "kimi-code", "category": "tool result", "tokens": 450, "share": 0.375},
    {"source": "kimi-code", "category": "user", "tokens": 450, "share": 0.375},
    {"source": "kimi-code", "category": "assistant", "tokens": 300, "share": 0.25},
]


@unittest.skipUnless(mpl_available, "matplotlib not installed")
class CategoryCompositionTest(unittest.TestCase):
    def _render(self, tmp: str, rows=ROWS, **kwargs):
        out = Path(tmp) / "figs"
        png = render_category_composition(rows, out, **kwargs)
        sidecar = json.loads((out / "category_composition.json").read_text())
        return png, sidecar

    def test_renders_png_and_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            png, sidecar = self._render(tmp, data_date="2026-09-20")
            self.assertTrue(png.exists())
            self.assertGreater(png.stat().st_size, 5000)  # non-trivial image
            self.assertEqual(
                [s["source"] for s in sidecar["sources"]],
                ["claude-code", "codex", "kimi-code"],
            )
            self.assertEqual(
                sidecar["category_order"],
                ["system", "harness context", "developer", "user",
                 "assistant", "tool result"],
            )

    def test_missing_categories_render_as_zero_segments(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, sidecar = self._render(tmp)
            kimi = next(s for s in sidecar["sources"] if s["source"] == "kimi-code")
            by_cat = {c["category"]: c for c in kimi["categories"]}
            self.assertEqual(by_cat["system"]["tokens"], 0)  # absent, not an error
            self.assertEqual(by_cat["system"]["share"], 0.0)
            self.assertEqual(by_cat["tool result"]["tokens"], 450)

    def test_tool_result_is_largest_segment_everywhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, sidecar = self._render(tmp)
            for source in sidecar["sources"]:
                shares = [c["share"] for c in source["categories"]]
                tool = next(
                    c["share"] for c in source["categories"]
                    if c["category"] == "tool result"
                )
                self.assertEqual(tool, max(shares))

    def test_developer_segment_present_for_codex_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, sidecar = self._render(tmp)
            by_source = {s["source"]: s for s in sidecar["sources"]}
            codex = {c["category"]: c for c in by_source["codex"]["categories"]}
            self.assertEqual(codex["developer"]["tokens"], 300)
            claude = {
                c["category"]: c for c in by_source["claude-code"]["categories"]
            }
            self.assertEqual(claude["developer"]["tokens"], 0)  # absent, not an error
            self.assertEqual(claude["developer"]["share"], 0.0)

    def test_grand_total_rows_ignored_and_shares_recomputed(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, sidecar = self._render(tmp)
            self.assertNotIn("all", [s["source"] for s in sidecar["sources"]])
            claude = next(
                s for s in sidecar["sources"] if s["source"] == "claude-code"
            )
            total = sum(c["share"] for c in claude["categories"])
            self.assertAlmostEqual(total, 1.0)

    def test_regeneration_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            png1, sidecar1 = self._render(tmp, data_date="2026-09-20")
            png2, sidecar2 = self._render(
                tmp, data_date="2026-09-20"
            )
            self.assertEqual(png1.read_bytes(), png2.read_bytes())
            self.assertEqual(sidecar1, sidecar2)

    def test_empty_data_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                render_category_composition([], Path(tmp))
            with self.assertRaises(ValueError):
                render_category_composition(
                    [{"source": "all", "category": "user", "tokens": 10}],
                    Path(tmp),
                )


if __name__ == "__main__":
    unittest.main()
