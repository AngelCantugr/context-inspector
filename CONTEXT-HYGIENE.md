# Context Hygiene Playbook

Rules and configs to keep agent context windows lean, distilled from a full
sweep of 1,100+ local sessions (Claude Code, Codex CLI, Kimi Code) analyzed
with this tool. Findings: shell output ≈ 66% of all tool-result tokens;
file reads ≈ 12%; one unbounded REPL dump once cost 3.7M tokens.

Organized as: **Quick wins** (paste into `CLAUDE.md` / `AGENTS.md` — terse,
because every line is charged to every session) and **Advanced configs**
(require exploration, habit-building, harness setup, or per-repo tuning).

---

## Quick wins — paste into CLAUDE.md / AGENTS.md

### Block 1 — Test & build output

```markdown
## Test & build output
- Run tests targeted (specific file/test); never full suites unless asked.
- Cap output: pytest `-x --tb=short -q`, builds `2>&1 | tail -50`.
- Report the final summary line + first failure only. Never paste full logs.
- Output >100 lines: summarize it, don't re-run to see more.
```

### Block 2 — Structured data & logs

```markdown
## Structured data & logs
- Never cat whole JSON/CSV/log files. Discover first: `jq 'keys'`, `head`, `wc -l`.
- Extract slices with jq/grep/awk — show fields, not records.
- Logs: filter by level, `tail` by default, aggregate (`uniq -c`) over raw lines.
- Files >500 lines: summarize structure before reading content.
```

### Block 3 — Session memory (for long tasks)

```markdown
## Long tasks
- Keep NOTES.md updated: decisions, current state, next steps.
- Read NOTES.md at session start instead of asking for a recap.
- Prefer writing findings to a file over long chat summaries.
```

---

## Advanced configs

### A. Harness hooks (needs per-harness exploration)

Hard enforcement at the moment of the bad habit, instead of rules that can
be ignored. Explore each harness's hook API before relying on it:

- **Claude Code** — `PreToolUse` hooks can *block* commands matching a
  pattern (e.g. `cat` on `*.json`/`*.log` >500 lines, test commands with no
  output-limiting flags) with a corrective reason ("use `jq`/`tail`").
  `PostToolUse` cannot silently rewrite tool output — use it to detect and
  warn, not to edit.
- **Kimi Code** — hook events exist in session logs (SessionStart observed);
  verify Pre/PostToolUse support and output-modification semantics before
  building on it.
- **Codex CLI** — fewer hook points; check current docs for tool-approval
  hooks that can enforce command patterns.

### B. Wrapper scripts (per-repo, high value)

Rules get forgotten; wrappers make the good path the default:

- `run_tests` — detects ecosystem (pytest/jest/go/cargo), applies quiet
  flags + `tail`, returns summary + first failure. Agents call `run_tests`
  instead of raw test commands.
- `query_json <file> <jq-filter>` — runs jq server-side, returns only the
  slice. The model never sees the file, only the answer.
- `ghq '<endpoint>' '<jq-filter>'` — same for API calls: filter in the pipe.

Worth doing once in your dotfiles and symlinking into repos.

### C. Query-shaped tools in your own agents (infrastructure)

For the P1 agent toolkit: make tool-result shaping structural, not
instructional. Tools take queries and return answers; full payloads go to
disk with a reference path. Cap results at N tokens (head+tail), and log
every truncation so the inspector can measure it.

### D. Session & memory habits (habit-building)

- One session per subtask; start fresh when the task pivots.
- NOTES.md as external memory (see Block 3) — 2k tokens once beats 300k
  of accumulated history.
- Delegate exploration to subagents so their context junk stays out of the
  main window (already a strength: 2,600+ subagent runs observed).
- Batch small one-off tasks: tiny sessions pay the full fixed tax (a
  Codex review-mode session measured 96% system prompt).

### E. Model & harness settings (per-harness config)

- Route mechanical turns to cheaper models; long sessions cost linearly.
- Check each harness's auto-compact threshold; lower it for tool-heavy work.
- Suppress verbose tool acknowledgement output where configurable
  (duplicate "file updated" boilerplate was the top duplicate category).
- Keep the repo instructions file dense — it is a fixed per-session tax
  (measured: Codex injected context ≈ 26–35% of tokens; Claude system
  prompt ≈ 8k tokens).

### F. Per-repo tuning (varies by target)

- Monorepo: scope test/build commands to the affected package; never root
  suites.
- Generated files, lockfiles, minified bundles: add to "never read whole"
  list with the query-first rule.
- Note each repo's runner(s) explicitly in its AGENTS.md so Block 1's
  generic flags get ecosystem-specific.
- Data-heavy repos (fixtures, snapshots): prefer `jq` projections over
  reads; snapshot files are read-once, never cat'd.

### G. Measurement habit (ongoing)

- Monthly: `python3 -m context_inspector sweep` — track median window per
  harness and tokens/call for Bash/exec/Read.
- Baseline (2026-09-21): medians — Claude ~54k, Codex ~33k, Kimi ~17k;
  Bash ~330 tok/call (Claude), exec ~2,236 (Codex), js_repl ~72,531.
- When an agent degrades mid-session: run the inspector on the session log
  before restarting — turn-40 stupidity is usually one giant tool result
  ten turns earlier.

---

*Source: `reports/2026-09-21-full-sweep-v2.md` + `reports/2026-09-21-verification.md`*
