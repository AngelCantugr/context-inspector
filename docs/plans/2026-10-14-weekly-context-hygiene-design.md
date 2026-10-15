# Weekly context-hygiene memo (AI-interpreted digest)

## Goal

A weekly, scheduled, AI-interpreted digest of the owner's agent context-window usage, filed as a note in their Tolaria/learning-depot vault, that the owner consumes during their Friday weekly-review ritual.

## Pipeline (four stages), orchestrated by one command `context-inspector weekly [--dry-run]`

### 1. Snapshot

Regenerate `sweep_results.jsonl` via the existing `sweep_runner.py`, then copy it to `reports/snapshots/sweep-YYYY-MM-DD.jsonl`. Snapshots are never overwritten; only the two most recent snapshots are needed for trend diffing. The step is idempotent within a week — a same-date re-run overwrites safely.

### 2. Outlier selection

A deterministic script (not the agent) diffs this week's snapshot against last week's, keyed by session path. Each session is scored on four signals:

- absolute window size
- week-over-week token growth
- wasted-tokens ratio
- bloat-signal count

The top N=3 outliers are written to a `deepdive.json` manifest, each annotated with the reason it was selected.

### 3. Interpretation

A LangGraph agent (Anthropic models, key from the `ANTHROPIC_API_KEY` env var) reads the snapshots plus query-pack outputs and runs the deep-dives. The tool surface is hard-coded and inspect-only:

- `analyze_session(path)`
- `run_query(name)`
- `read_window(path, start_line, n)` — a bounded transcript peek

Graph: `load_context → deep_dive (one iteration per outlier) → interpret → compose_memo → file_note`. Every finding must carry a citation bound to a tool return (query output or `analyze`/`read_window` evidence); the compose stage rejects uncited findings.

### 4. Filing

The memo is written to the vault's reports folder as a dated markdown note. Frontmatter is generated deterministically: `type: report`, `date`, and a link to the previous week's note by the naming convention `context-hygiene-weekly-<year>-W<week>`.

## Memo content shape (fixed sections)

- **(a) Findings** — for each deep-dive: the pattern, the likely cause, and one concrete prescription.
- **(b) Trend narrative** — the week-over-week story connecting the findings.
- **(c) One forward-looking context-hygiene experiment** for next week, shaped so the weekly-review ritual can pick it up.

The first-ever run files a "baseline" memo with no trend sections, explicitly labeled as such.

## Model

Anthropic via the `anthropic` SDK. Default: a current Claude Sonnet-class model (cost matters on a schedule). The model id is configurable via flag/env; the exact version is pinned at implementation time. Deterministic stages stay outside the graph — the agent only interprets, never counts.

## Packaging (uv migration)

- Build backend: setuptools → hatchling.
- Commit `uv.lock`; pin `.python-version`.
- Dev deps in `[dependency-groups].dev`.
- README install section rewritten to uv: `uv tool install .`, `uv sync --extra analytics --extra agent`.
- New optional extra `agent = ["langgraph", "anthropic"]`, imported **only** inside `context_inspector.agent`, so the base CLI stays stdlib-only and zero-dependency at startup.
- New layout: `context_inspector/agent/` (graph, tools, prompts, memo template), `context_inspector/weekly.py` (orchestrator), `reports/snapshots/`, tests mirroring the split.

## Cron wiring

Friday 2:00 PM, a project `local_conversation` Automation in the Kimi Work workspace whose entire instruction is: run `cd context-inspector && uv run --extra agent context-inspector weekly` and report failures. This is a manual setup step done AFTER the feature is implemented (see issues).

## Error handling

- Zero sessions found → abort loudly.
- Missing `ANTHROPIC_API_KEY` or vault path → preflight error before any API call.
- Agent-stage API failure → one backoff retry, then hard fail (skip the week rather than file a half-invented memo).
- Cost bounds: capped transcript reads per outlier, capped graph iterations.
- `--dry-run` prints the memo to stdout and never touches the vault.

## Testing

- stdlib unittest under `uv run`.
- Fixture-driven tests for snapshot/diff/outlier scoring (two small JSONL fixtures), including idempotency and the baseline path.
- Mocked-model agent tests (Anthropic client injected; canned responses): memo citations present, uncited findings rejected, iteration caps enforced, one-retry-then-fail.
- One golden-file test: fake week (fixtures → mocked graph → rendered markdown) vs a checked-in expected note.
- CLI smoke tests: `--dry-run` never touches the vault; missing key fails before client construction.
- Manual validation: one `--dry-run` against real snapshots before enabling the cron.

Out of scope for v1: LLM-output evals, multi-user, live tracing.
