# L1 Analytics Query Pack Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `sweep_results.jsonl` queryable via DuckDB — loader + schema (#1), 10 canonical SQL queries (#2), runner CLI + fixture tests (#3) — behind an `[analytics]` extra, base CLI untouched.

**Architecture:** `context_inspector/analytics/` holds the loader (explicit-schema `read_json` into typed `sessions` table + `categories`/`tools`/`signal_labels` views), the query pack (`queries/*.sql` + `queries/manifest.json`), and the runner (stdlib markdown/JSON writers). `cli.py` gains a `query` subcommand that imports analytics lazily. See `docs/plans/2026-09-22-l1-analytics-design.md` (same branch) for the full design and rationale.

**Tech Stack:** Python ≥3.10 stdlib + `duckdb>=1.0` (optional extra only). Tests: stdlib `unittest`. Env: `uv` (`uv venv`, `uv pip install -e ".[analytics]"`, `uv run ...`) — never plain pip.

> **Doc note:** code listings below are the as-planned baseline. Hardening commits d4410ac (NULLIF guards), 41ca6a7 (strict JSON, _fmt precision), and cde3a8c (duckdb.Error handling) supersede the embedded snippets — shipped code is authoritative.

**Hard rules (from the mission):** no `import duckdb` at base-package import time; acceptance criteria in issues #1–#3 are the contract — do not edit them; no new repo-root files beyond what the issues specify; do NOT close the issues.

---

### Task 0: Environment, real data, issue comments (mission Phase 0)

**Files:** none (setup only)

**Step 1: Create the dev env**

```bash
uv venv && uv pip install -e ".[analytics]"
```

Expected: succeeds; `uv run python -c "import duckdb; print(duckdb.__version__)"` prints ≥1.0. (The `[analytics]` extra is added in Task 1 — if this fails now, do Task 1 Step 2 first and re-run.)

**Step 2: Generate the real `sweep_results.jsonl` (skip if it already exists at repo root)**

```bash
uv run python sweep_runner.py   # resumable, 240s per run — re-run until it prints DONE
uv run python -m context_inspector sweep   # sanity check
```

**Step 3: Post intent comments on the issues (one paragraph each)**

```bash
gh issue comment 1 --repo AngelCantugr/context-inspector --body "Implementing on branch \`feature/l1-analytics\`. Approach: explicit-schema \`read_json\` into a typed \`sessions\` table (MAP/LIST columns instead of delimited text — rationale in the design doc), \`categories\`/\`tools\`/\`signal_labels\` views, lazy duckdb import mirroring \`estimator.get_tokenizer\`, \`[analytics]\` extra. Design: docs/plans/2026-09-22-l1-analytics-design.md on the branch."
gh issue comment 2 --repo AngelCantugr/context-inspector --body "Approach: 10 .sql files under context_inspector/analytics/queries/ with name/description/caveats headers, manifest.json (stdlib json, no YAML) mapping name → file → columns → params. Shared semantics: error records excluded, non-empty = total_tokens>0 for medians, token-weighted shares. Validated against a hand-computed fixture, then the real sweep."
gh issue comment 3 --repo AngelCantugr/context-inspector --body "Approach: \`query\` subparser in cli.py (analytics imported lazily inside the branch), runner.py with a tiny stdlib GFM table renderer, reports/queries/ default out dir, tests/fixtures/sweep_results.jsonl with hand-computable values, stdlib unittest with skipUnless(duckdb)."
```

**Step 4: Commit checkpoint** — nothing to commit yet; verify `git branch --show-current` = `feature/l1-analytics`.

---

### Task 1: `[analytics]` extra + package skeleton

**Files:**
- Modify: `pyproject.toml` (add one line under `[project.optional-dependencies]`)
- Create: `context_inspector/analytics/__init__.py`
- Test: `tests/test_loader.py` (skeleton), `tests/__init__.py`

**Step 1: Write the failing test**

`tests/__init__.py` — empty file.

`tests/test_loader.py`:

```python
"""Loader tests (#1). Expected values are hand-computed from
tests/fixtures/sweep_results.jsonl — see FIXTURE.md notes in test_queries."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "sweep_results.jsonl"

duckdb_available = importlib.util.find_spec("duckdb") is not None


@unittest.skipUnless(duckdb_available, "duckdb not installed (pip install '.[analytics]')")
class LoaderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from context_inspector.analytics import load_results

        cls.con = load_results(str(FIXTURE))

    def test_sessions_row_count(self):
        (n,) = self.con.sql("select count(*) from sessions").fetchone()
        self.assertEqual(n, 10)

    def test_categories_view_unnested(self):
        (n,) = self.con.sql("select count(*) from categories").fetchone()
        self.assertEqual(n, 19)

    def test_tools_view_unnested(self):
        (n,) = self.con.sql("select count(*) from tools").fetchone()
        self.assertEqual(n, 7)

    def test_old_record_without_tool_fields_loads(self):
        # record 8 (kimi-code) has no tool_tokens/tool_counts
        (n,) = self.con.sql(
            "select count(*) from tools where source = 'kimi-code' and calls is null"
        ).fetchone()
        self.assertEqual(n, 0)  # no tool rows at all for that session
        rows = self.con.sql(
            "select path, tokens, calls from tools where source = 'kimi-code'"
        ).fetchall()
        self.assertEqual(rows, [("/fake/kimi/sessions/ws1/conv1/agents/main/wire.jsonl", 450, 3)])

    def test_error_stub_loads_with_null_metrics(self):
        row = self.con.sql(
            "select total_tokens, error from sessions where error is not null"
        ).fetchall()
        self.assertEqual(row, [(None, "boom")])

    def test_session_date_path_derived(self):
        rows = self.con.sql(
            "select session_date from sessions where path like '%rollout-a1%'"
        ).fetchall()
        self.assertEqual(str(rows[0][0]), "2026-08-05")


class FriendlyErrorTest(unittest.TestCase):
    def test_loader_import_never_breaks_base_cli(self):
        # base CLI must import and run with no analytics deps touched
        import context_inspector.cli  # noqa: F401

        self.assertTrue(callable(context_inspector.cli.main))


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_loader -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'context_inspector.analytics'` (and fixture missing).

**Step 3: Write minimal implementation**

`pyproject.toml` — under `[project.optional-dependencies]` add:

```toml
analytics = ["duckdb>=1.0"]
```

`context_inspector/analytics/__init__.py`:

```python
"""Analytics layer (L1): DuckDB over sweep results.

Optional — requires the ``analytics`` extra (``pip install ".[analytics]"``).
duckdb is imported lazily inside ``loader.load_results`` so the base CLI
stays stdlib-only.
"""

from __future__ import annotations

from .loader import load_results

__all__ = ["load_results"]
```

Create an empty `context_inspector/analytics/loader.py` placeholder with just the module docstring for now (Task 3 fills it):

```python
"""Load sweep_results.jsonl into DuckDB with an explicit schema."""

from __future__ import annotations


def load_results(path: str):
    raise NotImplementedError  # Task 3
```

**Step 4: Run test to verify new failure mode**

Run: `uv run python -m unittest tests.test_loader -v`
Expected: FAIL with `NotImplementedError` in setUpClass, but `test_loader_import_never_breaks_base_cli` PASSES. Also verify base CLI unaffected: `uv run python -m context_inspector analyze examples/sample_transcript.json | head -3`.

**Step 5: Commit**

```bash
git add pyproject.toml context_inspector/analytics/ tests/
git commit -m "analytics: package skeleton + [analytics] extra (#1)"
```

---

### Task 2: Hand-computed fixture

**Files:**
- Create: `tests/fixtures/sweep_results.jsonl`

**Step 1: Write the fixture**

Exactly these 10 lines (one JSON object per line; absolute fake paths are fine — they never get opened, and keep Codex date segments parseable):

```jsonl
{"source": "codex", "path": "/fake/codex/sessions/2026/08/05/rollout-a1.jsonl", "total_tokens": 1000, "category_tokens": {"tool result": 800, "user": 100, "assistant": 100}, "n_duplicates": 0, "wasted_tokens": 0, "signals": ["tool results are 80% of the window — the chat is NOT what's eating your context"], "tool_tokens": {"exec": 800}, "tool_counts": {"exec": 4}}
{"source": "codex", "path": "/fake/codex/sessions/2026/08/20/rollout-a2.jsonl", "total_tokens": 500, "category_tokens": {"user": 200, "assistant": 300}, "n_duplicates": 2, "wasted_tokens": 150, "signals": ["2 exact duplicate message(s), ~150 tokens in repeated content"], "tool_tokens": {}, "tool_counts": {}}
{"source": "codex", "path": "/fake/codex/sessions/2026/09/02/rollout-a3.jsonl", "total_tokens": 2000, "category_tokens": {"tool result": 600, "system": 400, "user": 400, "assistant": 600}, "n_duplicates": 0, "wasted_tokens": 0, "signals": [], "tool_tokens": {"exec": 400, "apply_patch": 200}, "tool_counts": {"exec": 2, "apply_patch": 2}}
{"source": "claude-code", "path": "/fake/claude/projects/p1/s1.jsonl", "total_tokens": 4000, "category_tokens": {"tool result": 1700, "system": 800, "user": 800, "assistant": 700}, "n_duplicates": 3, "wasted_tokens": 900, "signals": ["tool results are 42% of the window — the chat is NOT what's eating your context", "3 exact duplicate message(s), ~900 tokens in repeated content"], "tool_tokens": {"Bash": 1300, "Read": 400}, "tool_counts": {"Bash": 6, "Read": 2}}
{"source": "claude-code", "path": "/fake/claude/projects/p1/s2.jsonl", "total_tokens": 100, "category_tokens": {"user": 100}, "n_duplicates": 0, "wasted_tokens": 0, "signals": [], "tool_tokens": {}, "tool_counts": {}}
{"source": "claude-code", "path": "/fake/claude/projects/p2/s3.jsonl", "total_tokens": 0, "category_tokens": {}, "n_duplicates": 0, "wasted_tokens": 0, "signals": [], "tool_tokens": {}, "tool_counts": {}}
{"source": "kimi-code", "path": "/fake/kimi/sessions/ws1/conv1/agents/main/wire.jsonl", "total_tokens": 900, "category_tokens": {"tool result": 450, "user": 450}, "n_duplicates": 1, "wasted_tokens": 450, "signals": ["1 exact duplicate message(s), ~450 tokens in repeated content"], "tool_tokens": {"Bash": 450}, "tool_counts": {"Bash": 3}}
{"source": "kimi-code", "path": "/fake/kimi/sessions/ws1/conv2/agents/main/wire.jsonl", "total_tokens": 300, "category_tokens": {"assistant": 300}, "n_duplicates": 0, "wasted_tokens": 0, "signals": []}
{"source": "codex", "path": "/fake/codex/sessions/2026/09/15/rollout-a4.jsonl", "total_tokens": 600, "category_tokens": {"tool result": 480, "user": 120}, "n_duplicates": 0, "wasted_tokens": 0, "signals": ["tool results are 80% of the window — the chat is NOT what's eating your context"], "tool_tokens": {"exec": 480}, "tool_counts": {"exec": 2}}
{"source": "codex", "path": "/fake/codex/sessions/2026/09/15/rollout-err.jsonl", "error": "boom"}
```

Coverage: all three sources; 80%-single-tool session (a1); known duplicates (a2, s1, conv1); zero-token (s3); error stub (rollout-err); old record missing `tool_tokens`/`tool_counts` (conv2); dated Codex paths spanning Aug + Sep 2026.

**Step 2: Hand-compute the golden numbers (this table is the test oracle — do not derive it by running code)**

Sessions (10 total, 9 valid after excluding the error stub; 8 non-empty after also excluding s3):

| query | expected on fixture |
|---|---|
| loader | sessions=10, categories=19 rows, tools=7 rows, signal_labels=6 rows |
| window_percentiles (total>0) | codex [500,600,1000,2000] p50=800; claude-code [100,4000] p50=2050; kimi-code [300,900] p50=600 |
| category_shares | codex: tool result 1880/4100≈0.4585, assistant 1000, user 820, system 400; grand-total row source='all': tool result 4030/9400≈0.4287 |
| tool_ranking (all) | exec 1680 tok / 8 calls / 210 per call; Bash 1750/9/194.4; Read 400/2/200; apply_patch 200/2/100; total tool tokens 4030; exec share 1680/4030≈0.4169 |
| whale_table (limit 10) | top: s1 4000 (claude, top_category_share 0.425), a3 2000, a1 1000, conv1 900, a4 600 — 8 rows total |
| duplicate_burden | 3 sessions with dupes (a2, s1, conv1); wasted 150+900+450=1500; share of all tokens 1500/9400≈0.1596; per source: codex 150/4100, claude-code 900/4100, kimi-code 450/1200 |
| monthly_trend | 2026-08: 2 sessions, 1500 tokens, median 750; 2026-09: 2 sessions, 2600 tokens, median 1300 (error stub and undated paths excluded) |
| tool_share_trend | 2026-08: exec 800 = 100%; 2026-09: exec 880/1080≈0.8148, apply_patch 200/1080≈0.1852 |
| signal_frequency | codex (4 valid): "tool results >40% of window" 2/4=0.5, "exact duplicates present" 1/4=0.25; claude-code (3): 1/3, 1/3; kimi-code (2): 0, 1/2 |
| category_outliers (threshold 60) | 4 rows: a1 tool result 80%, s2 user 100%, conv2 assistant 100%, a4 tool result 80% |
| bloat_signals_by_tool | "tool results >40% of window": exec 1280, Bash 1300, Read 400; "exact duplicates present": Bash 1750, Read 400 |

**Step 3: Verify the fixture parses**

Run: `uv run python -c "import json,pathlib; lines=pathlib.Path('tests/fixtures/sweep_results.jsonl').read_text().splitlines(); recs=[json.loads(l) for l in lines]; assert len(recs)==10; print('ok')"`
Expected: `ok`

**Step 4: Commit**

```bash
git add tests/fixtures/sweep_results.jsonl
git commit -m "tests: hand-computed sweep_results fixture (#3)"
```

---

### Task 3: Loader — `analytics/loader.py` (#1)

**Files:**
- Modify: `context_inspector/analytics/loader.py` (replace the Task-1 stub)
- Test: `tests/test_loader.py` (already written in Task 1)

**Step 1: Confirm the failing test state**

Run: `uv run python -m unittest tests.test_loader -v`
Expected: LoaderTest errors with `NotImplementedError`; FriendlyErrorTest passes.

**Step 2: Implement the loader**

```python
"""Load sweep_results.jsonl into DuckDB with an explicit schema.

The JSON is typed by DuckDB itself (``read_json`` with an explicit column
spec — no ``read_json_auto`` guessing, no Python-side expansion of nested
dicts). Records from older sweep versions may lack ``tool_tokens`` /
``tool_counts``, and sweep_runner writes error stubs with only
``source``/``path``/``error``: every missing field loads as NULL.

Public surface: ``load_results(path)`` returns a connection with the
``sessions`` table plus ``categories``, ``tools`` and ``signal_labels``
views ready to query.
"""

from __future__ import annotations

import os
import re
from datetime import date

_COLUMNS = """{
    source: 'TEXT', path: 'TEXT', total_tokens: 'BIGINT',
    category_tokens: 'MAP(VARCHAR, BIGINT)',
    tool_tokens: 'MAP(VARCHAR, BIGINT)', tool_counts: 'MAP(VARCHAR, BIGINT)',
    n_duplicates: 'INTEGER', wasted_tokens: 'BIGINT',
    signals: 'TEXT[]', error: 'TEXT'
}"""

_DATE_IN_PATH = re.compile(r"(20\d\d)/(\d{2})/(\d{2})")


def _duckdb():
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "duckdb is not installed. Install it with "
            "`pip install \"context-inspector[analytics]\"` "
            "(or `uv pip install -e \".[analytics]\"` from a clone)."
        ) from exc
    return duckdb


def _session_date(path: str) -> date | None:
    """Path-derived date first (Codex embeds YYYY/MM/DD), else file mtime."""
    m = _DATE_IN_PATH.search(path)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    try:
        return date.fromtimestamp(os.path.getmtime(path))
    except OSError:
        return None  # log moved/deleted or fixture path — monthly queries skip it


def load_results(path: str):
    """Load a sweep_results.jsonl file into an in-memory DuckDB database."""
    duckdb = _duckdb()
    if not os.path.exists(path):
        raise FileNotFoundError(f"sweep results file not found: {path}")
    con = duckdb.connect(":memory:")
    escaped = path.replace("'", "''")
    con.execute(
        f"CREATE TABLE sessions AS SELECT * FROM read_json('{escaped}', "
        f"format='newline_delimited', columns={_COLUMNS})"
    )
    con.execute("ALTER TABLE sessions ADD COLUMN session_date DATE")
    rows = con.execute("SELECT rowid, path FROM sessions").fetchall()
    con.executemany(
        "UPDATE sessions SET session_date = ? WHERE rowid = ?",
        [(d, rid) for rid, p in rows if (d := _session_date(p))],
    )
    con.execute(
        """
        CREATE VIEW categories AS
        SELECT path, source, entry.key AS category, entry.value AS tokens
        FROM sessions, unnest(map_entries(category_tokens)) AS u(entry)
        """
    )
    con.execute(
        """
        CREATE VIEW tools AS
        SELECT t.path, t.source, t.tool, t.tokens, c.calls
        FROM (SELECT path, source, e.key AS tool, e.value AS tokens
              FROM sessions, unnest(map_entries(tool_tokens)) AS u(e)) t
        LEFT JOIN (SELECT path, e.key AS tool, e.value AS calls
                   FROM sessions, unnest(map_entries(tool_counts)) AS u(e)) c
        USING (path, tool)
        """
    )
    # Canonical labels mirror sweep.SIGNAL_KEYS substring rules, so query
    # results match the sweep report. DISTINCT mirrors its set semantics.
    # Real sweeps also carry signal strings that match no key (e.g. the
    # single-tool-dominance signal) — the WHERE drops those, like
    # sweep._signal_hits ignoring unmatched strings.
    con.execute(
        """
        CREATE VIEW signal_labels AS
        SELECT DISTINCT path, source, label FROM (
            SELECT path, source,
                CASE
                    WHEN contains(sig, 'tool results') THEN 'tool results >40% of window'
                    WHEN contains(sig, 'system prompt') THEN 'system prompt >25% of window'
                    WHEN contains(sig, 'duplicate')     THEN 'exact duplicates present'
                    WHEN contains(sig, 'alone is')      THEN 'single message >30% of window'
                END AS label
            FROM sessions, unnest(signals) AS u(sig)
        ) WHERE label IS NOT NULL
        """
    )
    return con
```

Fallback if `MAP(VARCHAR, BIGINT)` in the `columns=` spec is rejected by the installed DuckDB: declare those three columns as `'JSON'` and change the views to unnest `map_entries(CAST(category_tokens AS MAP(VARCHAR, BIGINT)))` — verify with the fixture before committing either way.

**Step 3: Run the loader tests**

Run: `uv run python -m unittest tests.test_loader -v`
Expected: all PASS (7 tests).

**Step 4: Verify the #1 acceptance one-liner against real data**

```bash
uv run python -c "from context_inspector.analytics import load_results; con = load_results('sweep_results.jsonl'); print(con.sql('select count(*) from sessions').fetchall())"
```

Expected: `[(<real session count>,)]` in a couple of seconds. Also craft a tolerance probe:

```bash
printf '%s\n' '{"source":"codex","path":"/x/sessions/2026/01/01/r.jsonl","total_tokens":5,"category_tokens":{"user":5},"n_duplicates":0,"wasted_tokens":0,"signals":[]}' > /tmp/old_sweep.jsonl
uv run python -c "from context_inspector.analytics import load_results; con=load_results('/tmp/old_sweep.jsonl'); print(con.sql('select count(*) from tools').fetchall())"
```

Expected: `[(0,)]` — no crash on missing `tool_tokens`.

**Step 5: Commit**

```bash
git add context_inspector/analytics/loader.py tests/test_loader.py
git commit -m "analytics: DuckDB loader with typed schema + unnested views (#1)"
```

---

### Task 4: Manifest + queries batch A (monthly_trend, window_percentiles, category_shares)

**Files:**
- Create: `context_inspector/analytics/queries/manifest.json`
- Create: `context_inspector/analytics/queries/monthly_trend.sql`
- Create: `context_inspector/analytics/queries/window_percentiles.sql`
- Create: `context_inspector/analytics/queries/category_shares.sql`
- Test: `tests/test_queries.py`

**Step 1: Write the failing test**

`tests/test_queries.py`:

```python
"""Query-suite tests (#2). Every expected value below is hand-computed from
tests/fixtures/sweep_results.jsonl (see the golden table in
docs/plans/2026-09-22-l1-analytics-plan.md, Task 2)."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "sweep_results.jsonl"
duckdb_available = importlib.util.find_spec("duckdb") is not None


@unittest.skipUnless(duckdb_available, "duckdb not installed")
class QueryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from context_inspector.analytics import load_results
        from context_inspector.analytics.runner import load_manifest, run_query

        cls.con = load_results(str(FIXTURE))
        cls.manifest = {e.name: e for e in load_manifest()}
        cls.run_query = staticmethod(run_query)  # not "run" — would shadow TestCase.run

    def query(self, name):
        entry = self.manifest[name]
        columns, rows = self.run_query(self.con, entry)
        self.assertEqual(list(columns), entry.columns)
        return rows

    def test_manifest_covers_all_ten_queries(self):
        expected = {
            "monthly_trend", "window_percentiles", "category_shares",
            "tool_ranking", "whale_table", "duplicate_burden",
            "tool_share_trend", "signal_frequency", "category_outliers",
            "bloat_signals_by_tool",
        }
        self.assertEqual(set(self.manifest), expected)

    def test_monthly_trend(self):
        rows = self.query("monthly_trend")
        self.assertEqual(
            [(str(r[0]), r[1], r[2], r[3]) for r in rows],
            [("2026-08-01", 2, 1500, 750.0), ("2026-09-01", 2, 2600, 1300.0)],
        )

    def test_window_percentiles_median_excludes_empty(self):
        rows = {r[0]: r for r in self.query("window_percentiles")}
        self.assertEqual(rows["codex"][1], 800.0)
        self.assertEqual(rows["claude-code"][1], 2050.0)
        self.assertEqual(rows["kimi-code"][1], 600.0)

    def test_category_shares_token_weighted_with_grand_total(self):
        rows = self.query("category_shares")
        codex_tool = next(r for r in rows if r[0] == "codex" and r[1] == "tool result")
        self.assertEqual(codex_tool[2], 1880)
        self.assertAlmostEqual(codex_tool[3], 1880 / 4100, places=4)
        grand = next(r for r in rows if r[0] == "all" and r[1] == "tool result")
        self.assertEqual(grand[2], 4030)
        self.assertAlmostEqual(grand[3], 4030 / 9400, places=4)


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_queries -v`
Expected: FAIL — `ModuleNotFoundError: context_inspector.analytics.runner` (runner lands in Task 8; create a minimal `runner.py` now with only `QueryEntry`/`load_manifest`/`run_query` — full rendering comes later):

```python
"""Run canonical SQL queries and render results (markdown + JSON)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

QUERIES_DIR = Path(__file__).parent / "queries"


@dataclass
class QueryEntry:
    name: str
    file: str
    description: str
    columns: list[str]
    params: list[dict] = field(default_factory=list)


def load_manifest() -> list[QueryEntry]:
    data = json.loads((QUERIES_DIR / "manifest.json").read_text(encoding="utf-8"))
    return [QueryEntry(**entry) for entry in data["queries"]]


def run_query(con, entry: QueryEntry, params: list | None = None):
    sql = (QUERIES_DIR / entry.file).read_text(encoding="utf-8")
    bound = params if params is not None else [p["default"] for p in entry.params]
    cur = con.execute(sql, bound) if bound else con.execute(sql)
    columns = [d[0] for d in cur.description]
    return columns, cur.fetchall()
```

**Step 3: Write the manifest + batch-A SQL files**

`context_inspector/analytics/queries/manifest.json` (Task 4 ships entries 1–3; later tasks append theirs — final file must list all ten):

```json
{
  "queries": [
    {
      "name": "monthly_trend",
      "file": "monthly_trend.sql",
      "description": "Sessions, total tokens, median non-empty window per calendar month.",
      "columns": ["month", "sessions", "total_tokens", "median_window"],
      "params": []
    },
    {
      "name": "window_percentiles",
      "file": "window_percentiles.sql",
      "description": "p50/p90/p99 of total_tokens per source.",
      "columns": ["source", "p50", "p90", "p99"],
      "params": []
    },
    {
      "name": "category_shares",
      "file": "category_shares.sql",
      "description": "Token-weighted share per category per source, plus a source='all' grand-total row set.",
      "columns": ["source", "category", "tokens", "share"],
      "params": []
    }
  ]
}
```

`monthly_trend.sql`:

```sql
-- name: monthly_trend
-- description: Sessions, total tokens, median non-empty window per calendar month.
-- caveats: month comes from session_date (path-derived Codex date, else file
--   mtime at load time); sessions with no derivable date are excluded. Median
--   excludes zero-token sessions. Error records excluded.
SELECT date_trunc('month', session_date)::DATE AS month,
       count(*) AS sessions,
       sum(total_tokens) AS total_tokens,
       median(total_tokens) AS median_window
FROM sessions
WHERE session_date IS NOT NULL AND error IS NULL AND total_tokens > 0
GROUP BY 1 ORDER BY 1;
```

`window_percentiles.sql`:

```sql
-- name: window_percentiles
-- description: p50/p90/p99 of total_tokens per source.
-- caveats: non-empty sessions only (total_tokens > 0); continuous quantiles
--   (quantile_cont). Error records excluded.
SELECT source,
       quantile_cont(total_tokens, 0.5) AS p50,
       quantile_cont(total_tokens, 0.9) AS p90,
       quantile_cont(total_tokens, 0.99) AS p99
FROM sessions
WHERE error IS NULL AND total_tokens > 0
GROUP BY source ORDER BY source;
```

`category_shares.sql`:

```sql
-- name: category_shares
-- description: Token-weighted share per category per source, plus a
--   source='all' grand-total row set.
-- caveats: shares are token-weighted (sum of tokens), not per-session
--   averages — matches sweep.aggregate. Error records excluded.
WITH per_source AS (
    SELECT source, category, sum(tokens) AS tokens
    FROM categories c JOIN sessions s USING (path)
    WHERE s.error IS NULL
    GROUP BY source, category
), combined AS (
    SELECT source, category, tokens FROM per_source
    UNION ALL
    SELECT 'all', category, sum(tokens) FROM per_source GROUP BY category
)
SELECT source, category, tokens,
       tokens * 1.0 / sum(tokens) OVER (PARTITION BY source) AS share
FROM combined
ORDER BY source, tokens DESC;
```

**Step 4: Run tests**

Run: `uv run python -m unittest tests.test_queries -v`
Expected: 4 PASS (manifest test passes only once all ten entries exist — until then expect it to FAIL on the set difference; that's fine, or temporarily assert `issubset`. Prefer keeping it strict: red until Task 7 completes.)

**Step 5: Commit**

```bash
git add context_inspector/analytics/queries/ context_inspector/analytics/runner.py tests/test_queries.py
git commit -m "analytics: query manifest + monthly_trend, window_percentiles, category_shares (#2)"
```

---

### Task 5: Queries batch B (tool_ranking, whale_table, duplicate_burden)

**Files:**
- Create: `context_inspector/analytics/queries/tool_ranking.sql`
- Create: `context_inspector/analytics/queries/whale_table.sql`
- Create: `context_inspector/analytics/queries/duplicate_burden.sql`
- Modify: `context_inspector/analytics/queries/manifest.json` (append 3 entries)
- Modify: `tests/test_queries.py` (append tests)

**Step 1: Write the failing tests** (append to `tests/test_queries.py`)

```python
    def test_tool_ranking(self):
        rows = {r[1]: r for r in self.query("tool_ranking") if r[0] == "all"}
        self.assertEqual(rows["exec"][2:6], (1680, 1680 / 4030, 8, 210.0))
        self.assertEqual(rows["Bash"][2:6], (1750, 1750 / 4030, 9, 1750 / 9))
        self.assertEqual(rows["apply_patch"][2:6], (200, 200 / 4030, 2, 100.0))

    def test_whale_table(self):
        rows = self.query("whale_table")
        self.assertEqual(len(rows), 8)  # error stub + zero-token excluded
        self.assertEqual(rows[0][1:4],
                         ("/fake/claude/projects/p1/s1.jsonl", "claude-code", 4000))

    def test_duplicate_burden(self):
        rows = {r[0]: r for r in self.query("duplicate_burden")}
        self.assertEqual(rows["all"][1:4], (9, 3, 1500))
        self.assertAlmostEqual(rows["all"][4], 1500 / 9400, places=4)
        self.assertEqual(rows["kimi-code"][1:4], (2, 1, 450))
```

**Step 2: Run to verify failure** — `uv run python -m unittest tests.test_queries -v` → the three new tests FAIL (file not found / manifest key error).

**Step 3: Write the SQL**

`tool_ranking.sql`:

```sql
-- name: tool_ranking
-- description: Per tool — total tokens, share of tool-result tokens, call
--   count, tokens/call — overall (source='all') and per source.
-- caveats: tokens/call is NULL when tool_counts was absent (older sweeps).
--   Shares are of all tool-result tokens within the same source scope.
WITH scoped AS (
    SELECT t.source, t.tool, sum(t.tokens) AS tokens, sum(t.calls) AS calls
    FROM tools t JOIN sessions s USING (path)
    WHERE s.error IS NULL
    GROUP BY t.source, t.tool
), combined AS (
    SELECT source, tool, tokens, calls FROM scoped
    UNION ALL
    SELECT 'all', tool, sum(tokens), sum(calls) FROM scoped GROUP BY tool
)
SELECT source, tool, tokens,
       tokens * 1.0 / sum(tokens) OVER (PARTITION BY source) AS share,
       calls,
       tokens * 1.0 / calls AS tokens_per_call
FROM combined
ORDER BY source, tokens DESC;
```

`whale_table.sql`:

```sql
-- name: whale_table
-- description: Top-N sessions by total_tokens with source, dominant category,
--   duplicate count, wasted tokens, signals fired.
-- caveats: dominant category = largest category_tokens entry (ties broken by
--   name). Error records and zero-token sessions excluded. N is a bound
--   parameter (default 10).
WITH dominant AS (
    SELECT path, category, tokens,
           row_number() OVER (PARTITION BY path ORDER BY tokens DESC, category) AS rn
    FROM categories
)
SELECT row_number() OVER (ORDER BY s.total_tokens DESC) AS rank,
       s.path, s.source, s.total_tokens,
       d.category AS top_category,
       d.tokens * 1.0 / s.total_tokens AS top_category_share,
       s.n_duplicates, s.wasted_tokens,
       array_to_string(s.signals, '; ') AS signals
FROM sessions s LEFT JOIN dominant d ON d.path = s.path AND d.rn = 1
WHERE s.error IS NULL AND s.total_tokens > 0
ORDER BY s.total_tokens DESC
LIMIT ?;
```

Manifest params entry: `"params": [{"name": "limit", "default": 10}]`,
columns: `["rank", "path", "source", "total_tokens", "top_category", "top_category_share", "n_duplicates", "wasted_tokens", "signals"]`.

`duplicate_burden.sql`:

```sql
-- name: duplicate_burden
-- description: Sessions with duplicates and tokens in repeated content —
--   count + share of all tokens, per source plus 'all'.
-- caveats: share_of_tokens = wasted_tokens / all tokens (including sessions
--   without duplicates). Error records excluded; zero-token sessions count
--   toward session totals but add no tokens.
WITH per_source AS (
    SELECT source, count(*) AS sessions,
           count(*) FILTER (n_duplicates > 0) AS sessions_with_duplicates,
           sum(wasted_tokens) AS wasted_tokens,
           sum(total_tokens) AS total_tokens
    FROM sessions WHERE error IS NULL
    GROUP BY source
), combined AS (
    SELECT * FROM per_source
    UNION ALL
    SELECT 'all', sum(sessions), sum(sessions_with_duplicates),
           sum(wasted_tokens), sum(total_tokens) FROM per_source
)
SELECT source, sessions, sessions_with_duplicates, wasted_tokens,
       wasted_tokens * 1.0 / total_tokens AS share_of_tokens
FROM combined ORDER BY source;
```

**Step 4: Run tests** — `uv run python -m unittest tests.test_queries -v` → batch-B tests PASS.

**Step 5: Commit**

```bash
git add context_inspector/analytics/queries/ tests/test_queries.py
git commit -m "analytics: tool_ranking, whale_table, duplicate_burden (#2)"
```

---

### Task 6: Queries batch C (tool_share_trend, signal_frequency)

**Files:**
- Create: `context_inspector/analytics/queries/tool_share_trend.sql`
- Create: `context_inspector/analytics/queries/signal_frequency.sql`
- Modify: `context_inspector/analytics/queries/manifest.json` (append 2 entries)
- Modify: `tests/test_queries.py` (append tests)

**Step 1: Failing tests**

```python
    def test_tool_share_trend(self):
        rows = self.query("tool_share_trend")
        aug = {r[1]: r for r in rows if str(r[0]) == "2026-08-01"}
        sep = {r[1]: r for r in rows if str(r[0]) == "2026-09-01"}
        self.assertAlmostEqual(aug["exec"][3], 1.0, places=4)
        self.assertAlmostEqual(sep["exec"][3], 880 / 1080, places=4)
        self.assertAlmostEqual(sep["apply_patch"][3], 200 / 1080, places=4)

    def test_signal_frequency(self):
        rows = self.query("signal_frequency")
        codex = {r[1]: r for r in rows if r[0] == "codex"}
        self.assertEqual(codex["tool results >40% of window"][2:], (4, 0.5))
        self.assertEqual(codex["exact duplicates present"][2:], (4, 0.25))
        kimi = {r[1]: r for r in rows if r[0] == "kimi-code"}
        self.assertEqual(kimi["exact duplicates present"][2:], (2, 0.5))
```

**Step 2: Run to verify failure**, then write the SQL.

`tool_share_trend.sql`:

```sql
-- name: tool_share_trend
-- description: Per-tool share of tool-result tokens per calendar month.
-- caveats: only sessions with a derivable session_date (see monthly_trend).
--   Detects e.g. shell output growing over time. Error records excluded.
SELECT month, tool, tokens,
       tokens * 1.0 / sum(tokens) OVER (PARTITION BY month) AS share
FROM (
    SELECT date_trunc('month', s.session_date)::DATE AS month,
           t.tool, sum(t.tokens) AS tokens
    FROM tools t JOIN sessions s USING (path)
    WHERE s.error IS NULL AND s.session_date IS NOT NULL
    GROUP BY 1, 2
) ORDER BY month, tokens DESC;
```

columns: `["month", "tool", "tokens", "share"]`.

`signal_frequency.sql`:

```sql
-- name: signal_frequency
-- description: Share of sessions firing each bloat signal, per source.
--   Mirrors the sweep report's signal_frequency block.
-- caveats: labels come from the signal_labels view (same substring rules as
--   sweep.SIGNAL_KEYS). Denominator = valid sessions per source; signals on
--   error records are ignored (there are none by construction).
WITH denom AS (
    SELECT source, count(*) AS n FROM sessions WHERE error IS NULL GROUP BY source
)
SELECT l.source, l.label, d.n AS sessions,
       count(*) * 1.0 / d.n AS share
FROM signal_labels l JOIN denom d USING (source)
JOIN sessions s ON s.path = l.path AND s.error IS NULL
GROUP BY l.source, l.label, d.n
ORDER BY l.source, share DESC;
```

columns: `["source", "label", "sessions", "share"]`.

**Step 3: Run tests** → PASS. **Step 4: Commit** `analytics: tool_share_trend, signal_frequency (#2)`.

---

### Task 7: Queries batch D (category_outliers, bloat_signals_by_tool) + manifest complete

**Files:**
- Create: `context_inspector/analytics/queries/category_outliers.sql`
- Create: `context_inspector/analytics/queries/bloat_signals_by_tool.sql`
- Modify: `context_inspector/analytics/queries/manifest.json` (final 2 entries — all ten now present)
- Modify: `tests/test_queries.py` (append tests)

**Step 1: Failing tests**

```python
    def test_category_outliers_default_threshold(self):
        rows = self.query("category_outliers")
        got = {(r[0].rsplit("/", 1)[-1], r[2], round(r[5], 2)) for r in rows}
        self.assertEqual(got, {
            ("rollout-a1.jsonl", "tool result", 0.8),
            ("s2.jsonl", "user", 1.0),
            ("wire.jsonl", "assistant", 1.0),
            ("rollout-a4.jsonl", "tool result", 0.8),
        })

    def test_bloat_signals_by_tool(self):
        rows = self.query("bloat_signals_by_tool")
        got = {(r[0], r[1]): r[2] for r in rows}
        self.assertEqual(got[("tool results >40% of window", "exec")], 1280)
        self.assertEqual(got[("tool results >40% of window", "Bash")], 1300)
        self.assertEqual(got[("exact duplicates present", "Bash")], 1750)
        self.assertEqual(got[("exact duplicates present", "Read")], 400)
```

**Step 2: Run to verify failure**, then write the SQL.

`category_outliers.sql`:

```sql
-- name: category_outliers
-- description: Sessions where a single category exceeds X% of the window.
-- caveats: threshold is a bound parameter (percent, default 60). Zero-token
--   sessions and error records excluded. Finds e.g. sessions that are 90%
--   one tool result.
SELECT s.path, s.source, c.category, c.tokens AS category_tokens,
       s.total_tokens, c.tokens * 1.0 / s.total_tokens AS share
FROM categories c JOIN sessions s USING (path)
WHERE s.error IS NULL AND s.total_tokens > 0
  AND c.tokens * 100.0 / s.total_tokens > ?
ORDER BY share DESC;
```

params: `[{"name": "threshold", "default": 60}]`; columns:
`["path", "source", "category", "category_tokens", "total_tokens", "share"]`.

`bloat_signals_by_tool.sql`:

```sql
-- name: bloat_signals_by_tool
-- description: Cross-tab — for sessions where a given signal fired, which
--   tools dominate (tool-result tokens per label × tool).
-- caveats: sessions without tool attribution contribute nothing (e.g. old
--   records). Powers the "what precedes runaway output" narrative.
SELECT l.label, t.tool, sum(t.tokens) AS tokens,
       count(DISTINCT t.path) AS sessions
FROM signal_labels l
JOIN tools t ON t.path = l.path
GROUP BY l.label, t.tool
ORDER BY l.label, tokens DESC;
```

columns: `["label", "tool", "tokens", "sessions"]`.

**Step 3: Run the full suite**

Run: `uv run python -m unittest discover tests -v`
Expected: all PASS, including `test_manifest_covers_all_ten_queries`.

**Step 4: Cross-query consistency check (integrator pass)**

- Every `.sql` header has `-- name:` matching its filename and `-- description:` matching the manifest entry.
- Every manifest `columns` list matches the query's SELECT output order.
- No SQL file contains `/Users/`, `sweep_results` paths, or machine-specific values: `grep -rn "Users\|angelcantu" context_inspector/analytics/` → no hits.

**Step 5: Commit**

```bash
git add context_inspector/analytics/queries/ tests/test_queries.py
git commit -m "analytics: category_outliers, bloat_signals_by_tool — query suite complete (#2)"
```

---

### Task 8: Runner rendering — markdown + JSON writers (#3)

**Files:**
- Modify: `context_inspector/analytics/runner.py` (add `render_markdown`, `run_queries`)
- Test: `tests/test_runner.py`

**Step 1: Write the failing test**

`tests/test_runner.py`:

```python
"""Runner rendering tests (#3)."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "sweep_results.jsonl"
duckdb_available = importlib.util.find_spec("duckdb") is not None


@unittest.skipUnless(duckdb_available, "duckdb not installed")
class RunnerTest(unittest.TestCase):
    def test_markdown_table_gfm_with_right_aligned_numbers(self):
        from context_inspector.analytics.runner import render_markdown, load_manifest

        entry = next(e for e in load_manifest() if e.name == "window_percentiles")
        md = render_markdown(entry, ["source", "p50"], [("codex", 800.0), ("kimi", None)])
        lines = md.splitlines()
        self.assertIn("| source | p50 |", lines)
        self.assertIn("|---|---:|", lines)  # numeric column right-aligned
        self.assertIn("| codex | 800.0 |", lines)
        self.assertIn("| kimi | — |", lines)  # NULL rendered as em dash

    def test_run_queries_writes_md_and_json(self):
        from context_inspector.analytics import load_results
        from context_inspector.analytics.runner import load_manifest, run_queries

        con = load_results(str(FIXTURE))
        with tempfile.TemporaryDirectory() as tmp:
            summaries = run_queries(con, ["window_percentiles"], Path(tmp))
            md = (tmp / "window_percentiles.md").read_text()
            data = json.loads((tmp / "window_percentiles.json").read_text())
        self.assertTrue(md.startswith("# window_percentiles"))
        self.assertEqual({r["source"] for r in data},
                         {"codex", "claude-code", "kimi-code"})
        self.assertEqual(len(summaries), 1)
        self.assertIn("window_percentiles", summaries[0])


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run to verify failure** — `uv run python -m unittest tests.test_runner -v` → FAIL (`render_markdown` missing).

**Step 3: Implement** (append to `runner.py`)

```python
def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def render_markdown(entry: QueryEntry, columns: list[str], rows: list[tuple]) -> str:
    """GitHub-flavored markdown table; numeric columns right-aligned."""
    numeric = [
        rows and any(_is_number(r[i]) for r in rows) for i in range(len(columns))
    ]
    lines = [f"# {entry.name}", "", entry.description, ""]
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("|" + "|".join("---:" if numeric[i] else "---" for i in range(len(columns))) + "|")
    for row in rows:
        lines.append("| " + " | ".join(_fmt(v) for v in row) + " |")
    lines.append("")
    return "\n".join(lines)


def run_queries(con, names: list[str], outdir: Path) -> list[str]:
    """Run the named queries, write <name>.md + <name>.json, return summaries."""
    entries = {e.name: e for e in load_manifest()}
    outdir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for name in names:
        entry = entries[name]
        columns, rows = run_query(con, entry)
        (outdir / f"{name}.md").write_text(
            render_markdown(entry, columns, rows), encoding="utf-8"
        )
        (outdir / f"{name}.json").write_text(
            json.dumps([dict(zip(columns, map(_fmt_or_raw, row))) for row in rows],
                       indent=2, default=str),
            encoding="utf-8",
        )
        summaries.append(f"{name}: {len(rows)} rows -> {outdir / (name + '.md')}")
    return summaries


def _fmt_or_raw(value):
    """JSON keeps raw values (dates/decimals stringified via default=str)."""
    return value
```

(Keep `_fmt_or_raw` as a pass-through hook — JSON output must stay raw/typed; `json.dumps(default=str)` handles `date`/`Decimal`.)

**Step 4: Run tests** — `uv run python -m unittest tests.test_runner -v` → PASS.

**Step 5: Commit**

```bash
git add context_inspector/analytics/runner.py tests/test_runner.py
git commit -m "analytics: markdown/JSON query runner (#3)"
```

---

### Task 9: CLI `query` subcommand (#3)

**Files:**
- Modify: `context_inspector/cli.py`
- Test: `tests/test_cli_query.py`

**Step 1: Write the failing test**

`tests/test_cli_query.py`:

```python
"""CLI smoke test for the query subcommand (#3)."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from context_inspector.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "sweep_results.jsonl"
duckdb_available = importlib.util.find_spec("duckdb") is not None


@unittest.skipUnless(duckdb_available, "duckdb not installed")
class QueryCliTest(unittest.TestCase):
    def test_query_all_into_tempdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = main(["query", str(FIXTURE), "--out", tmp])
            self.assertEqual(rc, 0)
            out = Path(tmp)
            names = [p.stem for p in out.glob("*.md")]
            self.assertEqual(len(names), 10)
            data = json.loads((out / "duplicate_burden.json").read_text())
            total = next(r for r in data if r["source"] == "all")
            self.assertEqual(total["wasted_tokens"], 1500)

    def test_query_single(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = main(["query", str(FIXTURE), "--query", "whale_table", "--out", tmp])
            self.assertEqual(rc, 0)
            self.assertEqual([p.name for p in Path(tmp).glob("*.md")],
                             ["whale_table.md"])

    def test_unknown_query_name_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = main(["query", str(FIXTURE), "--query", "nope", "--out", tmp])
            self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run to verify failure** — `uv run python -m unittest tests.test_cli_query -v` → FAIL (argparse: invalid choice `query`).

**Step 3: Implement in `cli.py`**

Add the subparser next to `p_sweep` (no analytics import here — top of file stays stdlib-only):

```python
    p_query = sub.add_parser(
        "query", help="run the canonical SQL query pack over sweep results "
        "(requires the [analytics] extra)"
    )
    p_query.add_argument("sweep_results", help="path to sweep_results.jsonl")
    p_query.add_argument(
        "--query", dest="queries", action="append", metavar="NAME",
        help="run only this query (repeatable; default: all ten)",
    )
    p_query.add_argument(
        "--out", default="reports/queries", metavar="DIR",
        help="output directory (default: reports/queries)",
    )
    p_query.add_argument(
        "--json", action="store_true",
        help="also print full JSON results to stdout",
    )
```

Restructure the tokenizer setup so `query` never touches it — replace the block after `args = parser.parse_args(argv)`:

```python
    args = parser.parse_args(argv)

    if args.command == "query":
        return _run_query(args)

    try:
        tokenizer = get_tokenizer(args.tokenizer, args.encoding)
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
```

Add `_run_query` (lazy import lives here and only here):

```python
def _run_query(args: argparse.Namespace) -> int:
    try:
        from .analytics import load_results
        from .analytics.runner import load_manifest, run_queries
    except RuntimeError as exc:  # duckdb missing — friendly install hint
        print(f"error: {exc}", file=sys.stderr)
        return 1
    known = {e.name for e in load_manifest()}
    names = args.queries or [e.name for e in load_manifest()]
    unknown = [n for n in names if n not in known]
    if unknown:
        print(
            f"error: unknown query {unknown[0]!r} (available: {', '.join(sorted(known))})",
            file=sys.stderr,
        )
        return 2
    try:
        con = load_results(args.sweep_results)
    except (OSError, RuntimeError) as exc:
        print(f"error: could not load sweep results: {exc}", file=sys.stderr)
        return 1
    summaries = run_queries(con, names, Path(args.out))
    for line in summaries:
        print(line)
    if args.json:
        for name in names:
            print((Path(args.out) / f"{name}.json").read_text(encoding="utf-8"))
    return 0
```

(`Path` is already imported locally in the sweep branch — move `from pathlib import Path` to module top to share it, and drop the local import.)

**Step 4: Run the full suite**

Run: `uv run python -m unittest discover tests -v`
Expected: all PASS.

**Step 5: Verify the #3 acceptance criterion on real data**

```bash
uv run python -m context_inspector query sweep_results.jsonl
ls reports/queries/
```

Expected: ten one-line summaries in seconds; `reports/queries/` holds 10 `.md` + 10 `.json`, all non-empty. Spot-check: `head -12 reports/queries/tool_ranking.md` shows a shell tool (`exec`/`Bash` family) on top.

**Step 6: Commit**

```bash
git add context_inspector/cli.py tests/test_cli_query.py
git commit -m "cli: query subcommand for the analytics pack (#3)"
```

---

### Task 10: README "Analytics" section

**Files:**
- Modify: `README.md` (insert after the `sweep` usage block, before "Supported session logs")

**Step 1: Add the section**

```markdown
## Analytics (optional, DuckDB)

The `query` subcommand runs a canonical pack of ten SQL queries over
`sweep_results.jsonl` (produced by `python sweep_runner.py`) and writes
markdown + JSON tables per query:

```bash
pip install ".[analytics]"     # adds duckdb; base CLI stays stdlib-only
python -m context_inspector query sweep_results.jsonl
python -m context_inspector query sweep_results.jsonl --query tool_ranking --out reports/queries
```

Outputs land in `reports/queries/<name>.md` (human) and `<name>.json`
(machine). The queries themselves — one documented `.sql` file each — live in
`context_inspector/analytics/queries/`.
```

**Step 2: Commit**

```bash
git add README.md
git commit -m "readme: Analytics section (#3)"
```

---

### Task 11: Verification (mission Phase 4 — three independent lenses)

**Step 1: Fresh-clone verification**

```bash
git clone --branch feature/l1-analytics <repo-url> /tmp/ci-verify && cd /tmp/ci-verify
uv venv && uv pip install -e ".[analytics]"
uv run python -m unittest discover tests -v
uv run python -m context_inspector analyze examples/sample_transcript.json
uv run python -m context_inspector query <path-to-real-sweep_results.jsonl> --out /tmp/ci-verify-reports
```

Also verify the base install stays duckdb-free: `uv venv /tmp/ci-base && uv pip install --python /tmp/ci-base/bin/python .` from the clone, then `/tmp/ci-base/bin/python -m context_inspector sweep` works and `/tmp/ci-base/bin/python -c "import duckdb"` fails.

**Step 2: Spec compliance** — re-read issues #1–#3 and check every acceptance-criteria box against the branch. Any unchecked box → fix before Task 12 (no editing criteria to match the code).

**Step 3: Data verification on the real sweep** — run all ten queries and confirm structural expectations (NOT the author's exact numbers): tool results is the top category per source; shell-family tools lead `tool_ranking`; medians exclude zero-token sessions; no query returns empty or absurd results.

---

### Task 12: Finalize — issue comments + PR (mission Phase 5)

**Step 1: Triage verifier findings** — fix P1/P2 on this branch; comment justifications for any deferred P3s.

**Step 2: Update the three issues** with what was built and how each acceptance criterion was verified (one `gh issue comment` per issue).

**Step 3: Push and open the PR**

```bash
git push -u origin feature/l1-analytics
gh pr create --repo AngelCantugr/context-inspector \
  --title "[L1] Analytics query pack (closes #1, #2, #3 pending human verification)" \
  --body "<summary + per-issue acceptance checklist + verifier findings/resolutions + representative real-data tables>"
```

Do NOT close the issues — the human closes them after review.

---

## Definition of done (from the mission)

- PR open, tests green from a fresh clone in a fresh venv.
- All acceptance criteria from #1–#3 demonstrably met (checked list in the PR).
- Three verifier reports (fresh-clone, spec-compliance, data) attached as PR comments.
- No secrets, no absolute local paths, no changes outside issue scope.
