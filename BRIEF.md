# Project Brief — Context Inspector

- **ID:** P2 (supporting project)
- **Status:** next-up
- **Pairs with post:** B2 — "Context Engineering Is the New Prompt Engineering"

## Problem

When an agent degrades mid-run, we currently *guess* why. Context inspector turns "the context probably filled up with junk" into a per-message, per-category token breakdown with concrete bloat signals — the evidence base for the B2 post, and a reusable debugging tool afterward.

## Success criteria

- [x] Analyze an OpenAI-format message transcript (JSON) from the CLI
- [x] Report per-message tokens, cumulative window growth, and category totals (system / user / assistant / tool results)
- [x] Flag bloat signals: duplicated content, tool-result share, top offenders
- [x] Zero runtime dependencies beyond the Python stdlib (boring = durable)
- [x] Adapters for real harness session logs — Claude Code, Codex CLI, Kimi Code/Work (`--from`, auto-detected)
- [ ] (later, for the post) Run it against a real agent transcript, not the sample

## Scope

**In:**
- Heuristic token estimation (no API calls, no keys, works offline)
- Terminal table report + `--json` output
- Sample transcript showing realistic agent bloat

**Out (explicitly):**
- Real tokenizer (tiktoken) — a v2 option; heuristic keeps it dependency-free
- HTML/chart rendering — for the post, screenshots of terminal output are enough
- Live tracing of a running agent — v2, post-worthy on its own

## Tech choices

- Python 3, stdlib only
- OpenAI chat message format as input (portable across providers)

## Blog-material assessment

- Is this interesting enough to be a post itself? **Maybe later** (live tracing version)
- Is it better as a section inside post B2? **Yes** — it's the evidence engine for section 3 & 5
- The one surprising thing a reader would learn: tool *results* typically dwarf the conversation itself — the chat is not what's eating your window.

## Log

| Date | What happened |
|---|---|
| 2026-09-13 | Brief written; PoC scaffolded (estimator, analyzer, CLI, sample transcript) |
| 2026-09-21 | Harness adapters added (`adapters.py`): Claude Code, Codex CLI, Kimi Code/Work session logs → OpenAI format, auto-detected in CLI. Fixed duplicate detection (tool-call-only assistant turns no longer false-positive). First real-session numbers: Claude Code ~405k tokens accumulated (40% tool results, 194 dupes), Codex rollout 61% tool results, Kimi session dominated by assistant turns (system prompt hash-only in wire log). |
| 2026-09-21 | `sweep` subcommand added (`sweep.py` + resumable `sweep_runner.py`). Full sweep of 3,715 local sessions (2,975 Claude Code / 733 Codex / 7 Kimi, 2.4 GB of logs, zero errors). Aggregate report at `reports/2026-09-21-full-sweep.md`. Caveats: Claude Code median window ≈ 0 (most jsonl files are stub sessions — shares are token-weighted, dominated by large sessions); Codex `developer`-role messages (harness-injected context) kept as their own category; Kimi system prompt not countable (hash-only). |
| 2026-09-21 | Full sweep verified by 4 independent agents → `reports/2026-09-21-verification.md`. All numbers reproduced exactly; three explanations corrected: (1) heuristic underestimates real tokens ~2× (tiktoken ground truth) — 34.7M heuristic ≈ 69M real, never quote as billing; (2) Codex "35% injected" = 35% system+developer of which ~80% harness wrappers — and it's a *visibility* difference (Claude/Kimi logs don't expose system prompts at all); (3) duplicates are 55% tool-result boilerplate + repeated identical tool calls, 0.3% system-reminders, not agent bugs. Whale sessions confirmed real; codex whale is 93% two runaway tool dumps. Also learned: 2,608/2,975 claude-code files are zero-token subagent stubs; 9/15 large Claude logs contain compaction markers (totals = cumulative processed, not final window). |
| 2026-09-21 | v0.3.0 — improvements driven by the verification findings: (1) v2 heuristic estimator (long code/JSON words ≈ len/4 tokens; validated ±15% vs tiktoken, v1 was ~2× under) + optional `--tokenizer tiktoken` (lazy import, stdlib-only default preserved); (2) Claude Code adapter now reads `prompt_snapshot` attachments → system prompt visible in ~26% of logs; (3) Codex adapter surfaces `<environment_context>`/`<user_action>` as a `harness context` category; (4) sweep skips subagent stub files (2,608 zero-token files gone: 3,715 → 1,122 sessions) and prints per-source caveats; (5) "wasted tokens" reworded to "tokens in repeated content". v2 sweep: ~80M tokens total, report at `reports/2026-09-21-full-sweep-v2.md`. |
| 2026-09-21 | Per-tool attribution added (v0.3.1): analyzer maps tool_call ids → tool names and ranks tools by result tokens with per-call averages; new bloat signal when one tool produces >50% of tool-result tokens; sweep aggregates the ranking corpus-wide (overall + per-harness top 3). Validated against the ad-hoc analysis: exec 12.5M / Bash 8.0M / js_repl 4.7M tokens — shell output ≈ 66% of all tool-result tokens. v2 report regenerated with the tool section. |
| 2026-09-21 | Published as a standalone public repo: github.com/AngelCantugr/context-inspector (MIT, v0.3.1). |
