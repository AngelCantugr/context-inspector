# Context Inspector

See what is actually eating your agent's context window.

Takes an OpenAI-format chat transcript (the message array you'd send to the API)
and reports per-message tokens, category totals, and bloat signals — no API keys,
no dependencies beyond the Python standard library.

Built as the evidence engine for the blog post **"Context Engineering Is the New
Prompt Engineering"** ([project BRIEF](BRIEF.md) · see [CONTEXT-HYGIENE.md](CONTEXT-HYGIENE.md)
for the actionable rules distilled from analyzing 1,100+ real sessions).

## Install

Zero required dependencies (Python ≥ 3.10, stdlib only):

```bash
pip install context-inspector   # or: clone and pip install .
# optional, for real token counts: pip install "context-inspector[tiktoken]"
```

## Usage

```bash
# after install, the CLI is on your PATH:
context-inspector analyze examples/sample_transcript.json

# or run from a clone without installing:

# plain OpenAI-format message array
python -m context_inspector analyze examples/sample_transcript.json
python -m context_inspector analyze your_transcript.json --json

# real agent sessions — format auto-detected from the path
python -m context_inspector analyze ~/.claude/projects/<project>/<session>.jsonl
python -m context_inspector analyze ~/.codex/sessions/<date>/rollout-*.jsonl
python -m context_inspector analyze <kimi-code home>/sessions/<ws>/<conv>/agents/main/wire.jsonl

# or force a format
python -m context_inspector analyze <log> --from claude-code   # codex | kimi-code

# sweep: analyze EVERY local session across all harnesses and aggregate
python -m context_inspector sweep
python -m context_inspector sweep --save reports/sweep.md

# real token counts (requires: pip install tiktoken)
python -m context_inspector analyze <log> --tokenizer tiktoken
python -m context_inspector sweep --tokenizer tiktoken --save reports/sweep-tiktoken.md
```

Supported session logs: **Claude Code** (`~/.claude/projects/**`), **Codex CLI**
(`~/.codex/sessions/**/rollout-*.jsonl`), **Kimi Code / Kimi Work**
(`sessions/**/agents/main/wire.jsonl`). See `adapters.py` for per-harness
limitations (e.g. Kimi's wire log stores only hashes of the system prompt,
Claude Code subagent sidechains are skipped).

## What it measures

- **Per-message tokens** with cumulative window growth
- **Category totals** — system / user / assistant / tool results / tool-call args
- **Per-tool attribution** — which tools produced the tool results, ranked by
  tokens, with per-call averages (single sessions and corpus-wide in `sweep`)
- **Bloat signals** — duplicate content, tool-result share of the window,
  oversized single messages, oversized system prompt, one tool dominating output

## Design notes

- Token counts default to a **v2 heuristic** (CJK chars ~1 token each; words
  ~1 token, but long words ≥8 chars — JSON, code, URLs, minified blobs —
  ~1 token per 4 chars). Validated within ±15% of tiktoken o200k_base on
  real session logs; v1 undercounted code-heavy traffic ~2×. For real counts:
  `--tokenizer tiktoken` (optional dependency — the tool itself stays
  stdlib-only).
- Input is the raw message list, so it works on transcripts from any provider
  or agent framework that can dump its message history.

## Deliberately not here (v3 candidates)

- HTML report, live tracing of a running agent.
