# L1 Analytics Query Pack — Design

Date: 2026-09-22 · Branch: `feature/l1-analytics` · Closes (pending human review): #1, #2, #3

Turn `sweep_results.jsonl` into queryable, blog-grade tables using DuckDB,
gated behind an optional `[analytics]` extra so the base tool stays
stdlib-only. The three GitHub issues are the spec; this document records the
decisions the issues left open and the reasoning behind them.

## Architecture

```
sweep_results.jsonl
      │
      ▼  load_results(path)                     # analytics/loader.py
DuckDB connection (in-memory)
  ├─ table  sessions       — one row per record, typed columns (incl. nested MAP/LIST)
  ├─ view   categories     — (path, source, category, tokens)
  ├─ view   tools          — (path, source, tool, tokens, calls)
  ├─ view   signal_labels  — (path, source, label)  canonical signal labels
  └─ column sessions.session_date — DATE, derived post-load
      │
      ▼  run_queries(con, names, outdir)        # analytics/runner.py
  queries/manifest.json + queries/*.sql
      │
      ▼
reports/queries/<name>.md + <name>.json
```

New code lives entirely under `context_inspector/analytics/` plus a `query`
subcommand in `cli.py`, tests under `tests/`, one new extra in
`pyproject.toml`, and a README section. Nothing else at repo root.

## Loader and schema (#1)

`load_results(path) -> duckdb.DuckDBPyConnection` in
`context_inspector/analytics/loader.py`. duckdb is imported lazily inside the
function; on `ImportError` it raises `RuntimeError("duckdb is not installed.
Install it with `pip install \"context-inspector[analytics]\"` …")` —
mirroring `estimator.get_tokenizer`'s tiktoken pattern. `analytics/__init__.py`
imports no duckdb at module import time, so the base CLI is unaffected.

Ingestion uses `read_json(..., format='newline_delimited', columns={...})`
with an **explicit column spec** — no `read_json_auto` guessing. DuckDB reads
and types the file itself; Python never holds the whole file's nested dicts
(memory bounded by DuckDB, satisfying the 1,100+ record criterion).

`sessions` table columns:

| column | type | notes |
|---|---|---|
| `path` | TEXT | |
| `source` | TEXT | claude-code / codex / kimi-code |
| `total_tokens` | BIGINT | NULL on error records |
| `n_duplicates` | INTEGER | NULL on error/old records |
| `wasted_tokens` | BIGINT | NULL on error/old records |
| `error` | TEXT | set only on error stubs (`sweep_runner.py` writes these) |
| `signals` | TEXT[] | raw signal strings; NULL when absent |
| `category_tokens` | MAP(VARCHAR, BIGINT) | NULL when absent |
| `tool_tokens` | MAP(VARCHAR, BIGINT) | NULL when absent (older sweep versions) |
| `tool_counts` | MAP(VARCHAR, BIGINT) | NULL when absent |
| `session_date` | DATE | derived post-load (see below) |

Fields missing from a JSON record load as NULL — this is what makes
error stubs and old-version records tolerant by construction.

**Deviation from the issue's suggested schema, documented as invited:** the
issue suggested `signals` as a delimited text column (or a join table) and
implied unnesting the nested dicts into flat views. We instead keep the nested
fields as typed `MAP`/`LIST` columns and derive everything else as views.
Rationale: explicit JSON-schema ingestion gives typed nested columns for free;
`unnest(map_entries(...))` views are then one-liners with no delimiter
escaping and no Python-side expansion. The three public views match the issue
exactly:

- `categories(path, source, category, tokens)` — `unnest(map_entries(category_tokens))`
- `tools(path, source, tool, tokens, calls)` — `tool_tokens` unnested, LEFT
  JOIN `tool_counts` unnested (counts can be missing when tokens exist)
- `signal_labels(path, source, label)` — `unnest(signals)` mapped through the
  same four substring rules as `sweep.SIGNAL_KEYS` ("tool results" →
  "tool results >40% of window", etc.), so query #8/#10 match sweep-report
  semantics. Extra derived view beyond the issue's list; harmless, and keeps
  substring logic out of individual queries.

**`session_date` derivation** (decision the issue delegated: "file mtime or
path-derived date; document which"): path-derived first — Codex paths embed
`sessions/YYYY/MM/DD/`; otherwise the file's mtime if it still exists on this
machine; otherwise NULL. Monthly queries (q1, q7) filter `session_date IS NOT
NULL` and their header comments document the coverage caveat. Fixture records
use Codex-style dated paths so the monthly queries are hand-computable in
tests. Post-load derivation streams `(rowid, path)` and UPDATEs — O(n) rows,
bounded memory, milliseconds for ~1.1k sessions.

## Query suite (#2)

Ten `.sql` files under `context_inspector/analytics/queries/`, names exactly
as listed in the issue: `monthly_trend`, `window_percentiles`,
`category_shares`, `tool_ranking`, `whale_table`, `duplicate_burden`,
`tool_share_trend`, `signal_frequency`, `category_outliers`,
`bloat_signals_by_tool`.

Each file starts with a header comment block:

```sql
-- name: tool_ranking
-- description: Per-tool total tokens, share of tool-result tokens, call
--   count, and tokens/call — overall and per source.
-- caveats: tokens/call is NULL when tool_counts is missing (older sweeps)…
```

Shared semantics across all queries (documented in each header where relevant):

- "Non-empty sessions" means `total_tokens > 0` — medians/percentiles exclude
  zero-token sessions, matching sweep's intent (sweep's own median currently
  includes them; the queries document the stricter choice).
- Error records (`error IS NOT NULL`) are excluded from every query.
- Shares are token-weighted (sum of tokens, not per-session averages),
  matching `sweep.aggregate`.
- No hardcoded paths or machine-specific values; parameters are bound at run
  time.

**Manifest**: `queries/manifest.json` (JSON, not YAML — stdlib `json`
parseable, per the issue). One object per query:
`{name, file, description, columns, params}` where `columns` is the expected
output-column list (used by tests and the runner's JSON writer) and `params`
is an ordered list of `{name, default}` bound positionally (`?` placeholders
in the SQL). Only `whale_table` (`limit`, default 10) and
`category_outliers` (`threshold`, default 60) declare params. Located via
`Path(__file__).parent / "queries"` — deliberate choice over
`importlib.resources`: zero packaging changes (constraint 4), works from a
clone and editable install; a future wheel release can switch to resources.

## Runner + CLI (#3)

`context_inspector/analytics/runner.py`:

- `load_manifest() -> list[QueryEntry]`
- `run_query(con, entry, params=None) -> (columns, rows)` — reads the `.sql`,
  binds manifest-default params via `con.execute(sql, params)`
- `render_markdown(entry, columns, rows) -> str` — tiny stdlib GFM table
  renderer: pipe header, `---|---` separator, numbers right-aligned
  (`---:`), NULL rendered as `—`
- `run_queries(con, names, outdir) -> list[summary]` — writes
  `<name>.md` and `<name>.json` (list of row objects built from
  `description` + `fetchall()`), returns one-line summaries for stdout

CLI (`cli.py`, existing subparser style):

```
python -m context_inspector query <sweep_results.jsonl> [--query NAME ...] [--out DIR] [--json]
```

- No `--query` → all ten, in manifest order. Unknown names → stderr error
  listing valid names, exit 2 (argparse-style usage error).
- `--out` defaults to `reports/queries/`, created with
  `mkdir(parents=True, exist_ok=True)` (same pattern as `--save`).
- `.md` + `.json` always written; one-line summary per query on stdout.
  `--json` additionally prints the full JSON results to stdout.
- The `analytics` import happens **inside** the `query` branch only;
  `RuntimeError` from the loader (duckdb missing) → `error: …` on stderr,
  exit 1 — identical to the tokenizer error path. `analyze`/`sweep` never
  touch it.
- Tokenizer setup is skipped for `query` (today `main()` unconditionally calls
  `get_tokenizer`; it moves behind `if args.command in ("analyze", "sweep")`).

## Fixture + tests (#3)

`tests/fixtures/sweep_results.jsonl` — ~10 hand-writable records covering:
all three sources; one session that is 80% tool results from a single tool;
one with known duplicates/wasted tokens; one zero-token session; one error
stub; one record missing `tool_tokens`/`tool_counts`; Codex-style dated paths
spanning two months so `monthly_trend`/`tool_share_trend` have computable
expected values. Expected numbers are derived by hand in the test file
docstring/comments, not recomputed from the fixture by code.

Stdlib `unittest` only, `tests/__init__.py` so `python -m unittest discover
tests` works:

- `test_loader.py` — row counts, view unnesting, missing-field tolerance,
  error-stub handling.
- `test_queries.py` — all 10 queries return the documented columns and the
  hand-computed values on the fixture (including param defaults).
- `test_cli_query.py` — end-to-end `query` run into a `tempfile.TemporaryDirectory`,
  asserting files + JSON shape; plus a "duckdb missing" simulation isn't
  needed — instead all tests use `unittest.skipUnless(find_spec("duckdb"))`
  so a stdlib-only checkout still runs green (skips), honoring the
  zero-dependency ethos.

README gains a short "Analytics" section: `pip install ".[analytics]"`,
example `query` invocation, pointer to `queries/`.

## Error handling

- Missing/corrupt input file → clear stderr error, exit 1 (OSError/duckdb
  Error caught at the CLI boundary).
- duckdb not installed → RuntimeError with install hint (loader), exit 1.
- Empty result sets are valid output (e.g. a corpus with no dated sessions):
  the runner writes an empty table with headers, not a crash.
- Queries never assume a source exists — per-source breakdowns come from
  `GROUP BY source`, so a fixture with 3 sources and a real corpus with 1
  both work.

## Out of scope (per the issues)

Charts (#4–#9), L3 ML aids, persistence beyond "load this file now", CI,
release automation. GitHub issues are commented but not closed; the human
closes after review.
