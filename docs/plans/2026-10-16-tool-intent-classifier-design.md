# Design — Tool-intent classifier (rule-based exploration vs. build/test split)

Date: 2026-10-16 · Issue: gh#12 · Status: validated with user, ready for implementation plan

## Goal

`tool_ranking` answers "which tools eat context" but not "what the tool was
*doing*". 70% of tool-result tokens in the 2026-09-30 corpus (18.4M / 26.4M)
sit in the shell family (`Bash`/`exec`/`exec_command`/`js_repl`), where intent
lives in the command text. This adds a deterministic, stdlib-only intent
taxonomy so the Serena before/after question (does symbol-level exploration
displace dump-style exploration?) becomes queryable.

Explicitly out of scope: LLM/semantic classification, storing command text in
sweep results, back-classifying old `sweep_results.jsonl` records.

## Decision log

- **Scope**: intent surfaces in three places — analytics layer (required by
  the issue), the sweep report (`render_sweep`/`to_markdown`), and the
  per-transcript view (`render_text`). User picked the full surfacing.
- **Shell roster** aligns with `charts/tool_ranking.py::SHELL_FAMILY`
  (`Bash`, `bash`, `exec`, `exec_command`, `shell_command`, `shell`);
  `js_repl` gets its own `repl` bucket, never parsed as shell.
- **Fixtures**: new transcript fixtures live as JSON files under
  `tests/fixtures/` and are fed through `sweep_one()` end-to-end.
- **Real-corpus acceptance** (shell-family `other` ≤ ~40%; 2026-04-28 whale
  session ~99% `repl`) is verified by running a fresh sweep against the
  author's real claude-code / codex / kimi-code session data.

## §1 — `context_inspector/intent.py` (new module)

Module-level, versioned classification tables with a docstring stating the
corpus validation date (2026-09-30 sweep). Single public entry point:

```python
INTENT_VERSION = "2026-10-16.v1"

def classify(tool_name: str, arguments: str) -> str: ...
```

Buckets: `explore`, `mutate`, `delegate`, `meta`, `test-build`, `vcs`,
`repl`, `other`. Resolution order:

1. **Exact tool name** — `TOOL_INTENT` dict:
   - `explore`: Read, Grep, Glob, WebFetch, WebSearch;
     `mcp__serena__find_symbol`, `get_symbols_overview`,
     `search_for_pattern`, `read_file`
   - `mutate`: Edit, Write
   - `delegate`: Agent, SendMessage
   - `meta`: TodoWrite, TaskCreate, TaskList and plan-mode tools
2. **MCP prefix rule** — any other `mcp__serena__*` → `explore`; unknown
   `mcp__*` → `other`.
3. **`repl`** — `js_repl` (and future REPL tools): "command" is code, never
   shell-parsed. Not in `SHELL_FAMILY`.
4. **Shell family** — for `SHELL_TOOLS`: `json.loads` the arguments, take the
   command field, `shlex.split`, first token's basename, look up in
   `COMMAND_INTENT`:
   - `explore`: cat, ls, rg, grep, sed, head, tail, find, less, file, wc,
     diff, stat, tree, jq
   - `test-build`: pytest, make, npm, npx, cargo, go, cmake, tsc, build
   - `vcs`: git (plus `gh` — GitHub CLI, mixed intent like git)
   - unknown head → `other`
5. Anything else, missing, or unparseable → `other`.

`classify` never raises: json/shlex failures and non-dict argument shapes
fall through to `other`. No regex on content, no fuzzy matching.

## §2 — Analyzer & sweep wiring

- `analyzer.analyze()`: extend the `id2name` pass to capture
  `(name, arguments)` per call id and eagerly resolve
  `id2intent[cid] = classify(name, arguments)`. Tool-result messages resolve
  intent via `tool_call_id`; results with no matching call id fall back to
  `other`. Aggregate into new `Report.intent_tokens` / `Report.intent_counts`
  (both `field(default_factory=dict)` — external Report constructors stay
  compatible). `to_dict()` gains both fields.
- `sweep.SessionResult` gains the same two dicts, copied in `sweep_one()`,
  mirroring `tool_tokens`/`tool_counts`.
- Human-facing surfacing (per the scope decision):
  - `render_sweep()` / `to_markdown()`: a "tool intents" section — overall
    (`source='all'`) intent shares plus one `[source]` summary line each,
    mirroring the existing tool section shape.
  - `render_text()`: a "Top intents" block next to "Top tools", same top-5
    format.
- `sweep_runner.py`: record dict gains `intent_tokens` / `intent_counts`
  after `tool_tokens`/`tool_counts`. Error stubs unchanged.
- `tool_tokens`/`tool_counts` untouched everywhere — the classifier only
  adds attribution.

## §3 — Analytics: loader, views, queries

- `loader.py`: `_COLUMNS` gains `intent_tokens: 'MAP(VARCHAR, BIGINT)'`,
  `intent_counts: 'MAP(VARCHAR, BIGINT)'` after the tool maps. New `intents`
  view mirrors the `tools` view (unnest `intent_tokens`, LEFT JOIN
  `intent_counts` on `(path, intent)`). Old records load as NULL maps —
  `unnest` yields no rows — same tolerance pattern as `tool_tokens`.
- `intent_shares.sql`: per intent — tokens, share of tool-result tokens,
  calls, tokens/call — overall and per source. Structural clone of
  `tool_ranking.sql` over the `intents` view.
- `intent_trend.sql`: monthly variant via `date_trunc('month', session_date)`
  per intent (tokens + share), mirroring `tool_share_trend.sql`.
- `manifest.json`: entries for both, columns
  `source, intent, tokens, share, calls, tokens_per_call` and
  `month, intent, tokens, share`, with old-record NULL caveats.
- README Analytics section gains one line.

## §4 — Fixtures, tests, verification

- `tests/test_intent.py`: every classifier branch — exact names, MCP prefix,
  repl, shell parsing (flags, absolute paths via basename), malformed JSON /
  empty command / unknown binary → other, never raises.
- Analyzer tests: synthetic message lists with an injected counting
  tokenizer; `Bash(cat)`, `Bash(rg)`, `Bash(pytest)`, `Bash(make)`,
  `js_repl`, and a bare tool-result without matching call id → `other`.
  Assert exact splits; assert `tool_tokens`/`tool_counts` unchanged.
- Transcript fixtures as JSON under `tests/fixtures/`, driven through
  `sweep_one()` to prove `SessionResult` carries intents end-to-end.
- `tests/fixtures/sweep_results.jsonl` gains records with intent fields;
  old records stay intent-less to prove loader tolerance. `test_queries.py`
  manifest-coverage set gains both new queries with hand-computed expected
  rows. `test_loader.py` gains an intents-view test.
- Verification: `python -m unittest discover tests` green. Real-corpus
  criteria: fresh sweep over local claude-code / codex / kimi-code sessions;
  shell-family tokens must NOT collapse into `other` beyond ~40%, and the
  2026-04-28 whale session must classify ~99% `repl`.

## Acceptance criteria mapping (gh#12)

| Criterion | Where verified |
|---|---|
| Report/records carry intent fields; old records load | `test_loader.py`, loader NULL-maps |
| Fixture `cat`/`rg` vs `pytest`/`make` → expected splits | `test_intent.py`, analyzer tests, sweep_one fixture |
| Shell family `other` ≤ ~40% on real corpus | fresh sweep + `intent_shares` on real session data |
| Whale session ~99% `repl` | same run, whale row |
| unittest green; stdlib-only; README line | suite + `intent.py` imports (`json`, `os.path`, `shlex`) |
