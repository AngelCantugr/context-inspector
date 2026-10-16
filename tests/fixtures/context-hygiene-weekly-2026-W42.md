---
type: report
date: 2026-10-15
title: Context hygiene weekly 2026-W42
prev: context-hygiene-weekly-2026-W41
---

# Context hygiene weekly 2026-W42

## Findings

### 1. codex tool-result bloat

- **Session:** `/fake/sessions/a1.jsonl`
- **Likely cause:** /fake/sessions/a1.jsonl carries 2 bloat signal(s) and 10% wasted tokens
- **Prescription:** deduplicate repeated tool outputs before they are re-injected into the window
- **Evidence:** T1

### 2. claude-code tool-result bloat

- **Session:** `/fake/sessions/a4.jsonl`
- **Likely cause:** /fake/sessions/a4.jsonl carries 2 bloat signal(s) and 50% wasted tokens
- **Prescription:** deduplicate repeated tool outputs before they are re-injected into the window
- **Evidence:** T1

### 3. claude-code tool-result bloat

- **Session:** `/fake/sessions/a3.jsonl`
- **Likely cause:** /fake/sessions/a3.jsonl carries 3 bloat signal(s) and 50% wasted tokens
- **Prescription:** deduplicate repeated tool outputs before they are re-injected into the window
- **Evidence:** T1

## Trend narrative

Tool-result bloat is the through-line: all three outliers carry duplicate-heavy tool output, and the two growing sessions grew almost entirely in the tool-result category.

## Next week's experiment

For one week, truncate tool results older than the last two calls in every session and re-run the sweep — if wasted_tokens drops week-over-week, adopt the truncation as a standing habit.

## Evidence

### T1 — `run_query`

Arguments: `{"name": "whale_table"}`

```json
{
  "query": "whale_table",
  "description": "Top-N sessions by total_tokens with source, dominant category, duplicate count, wasted tokens, signals fired.",
  "columns": [
    "rank",
    "path",
    "source",
    "total_tokens",
    "top_category",
    "top_category_share",
    "n_duplicates",
    "wasted_tokens",
    "signals"
  ],
  "rows": [
    [
      1,
      "/fake/sessions/a1.jsonl",
      "codex",
      20000,
      "tool result",
      0.6,
      8,
      2000,
      "tool results are 60% of the window \u2014 the chat is NOT what's eating your context; 8 exact duplicate message(s), ~2000 tokens in repeated content"
    ],
    [
      2,
      "/fake/sessions/a4.jsonl",
      "claude-code",
      8000,
      "tool result",
      0.5,
      5,
      4000,
      "tool results are 50% of the window \u2014 the chat is NOT what's eating your context; 5 exact duplicate message(s), ~4000 tokens in repeated content"
    ],
    [
      3,
      "/fake/sessions/a3.jsonl",
      "claude-code",
      6000,
      "tool result",
      0.5,
      6,
      3000,
      "tool results are 50% of the window \u2014 the chat is NOT what's eating your context; 6 exact duplicate message(s), ~3000 tokens in repeated content; single message is 35% of the window"
    ],
    [
      4,
      "/fake/sessions/a2.jsonl",
      "codex",
      4000,
      "assistant",
      0.6,
      0,
      0,
      ""
    ]
  ],
  "row_count": 4
}
```

### T2 — `run_query`

Arguments: `{"name": "whale_table"}`

```json
{
  "query": "whale_table",
  "description": "Top-N sessions by total_tokens with source, dominant category, duplicate count, wasted tokens, signals fired.",
  "columns": [
    "rank",
    "path",
    "source",
    "total_tokens",
    "top_category",
    "top_category_share",
    "n_duplicates",
    "wasted_tokens",
    "signals"
  ],
  "rows": [
    [
      1,
      "/fake/sessions/a1.jsonl",
      "codex",
      20000,
      "tool result",
      0.6,
      8,
      2000,
      "tool results are 60% of the window \u2014 the chat is NOT what's eating your context; 8 exact duplicate message(s), ~2000 tokens in repeated content"
    ],
    [
      2,
      "/fake/sessions/a4.jsonl",
      "claude-code",
      8000,
      "tool result",
      0.5,
      5,
      4000,
      "tool results are 50% of the window \u2014 the chat is NOT what's eating your context; 5 exact duplicate message(s), ~4000 tokens in repeated content"
    ],
    [
      3,
      "/fake/sessions/a3.jsonl",
      "claude-code",
      6000,
      "tool result",
      0.5,
      6,
      3000,
      "tool results are 50% of the window \u2014 the chat is NOT what's eating your context; 6 exact duplicate message(s), ~3000 tokens in repeated content; single message is 35% of the window"
    ],
    [
      4,
      "/fake/sessions/a2.jsonl",
      "codex",
      4000,
      "assistant",
      0.6,
      0,
      0,
      ""
    ]
  ],
  "row_count": 4
}
```

### T3 — `run_query`

Arguments: `{"name": "whale_table"}`

```json
{
  "query": "whale_table",
  "description": "Top-N sessions by total_tokens with source, dominant category, duplicate count, wasted tokens, signals fired.",
  "columns": [
    "rank",
    "path",
    "source",
    "total_tokens",
    "top_category",
    "top_category_share",
    "n_duplicates",
    "wasted_tokens",
    "signals"
  ],
  "rows": [
    [
      1,
      "/fake/sessions/a1.jsonl",
      "codex",
      20000,
      "tool result",
      0.6,
      8,
      2000,
      "tool results are 60% of the window \u2014 the chat is NOT what's eating your context; 8 exact duplicate message(s), ~2000 tokens in repeated content"
    ],
    [
      2,
      "/fake/sessions/a4.jsonl",
      "claude-code",
      8000,
      "tool result",
      0.5,
      5,
      4000,
      "tool results are 50% of the window \u2014 the chat is NOT what's eating your context; 5 exact duplicate message(s), ~4000 tokens in repeated content"
    ],
    [
      3,
      "/fake/sessions/a3.jsonl",
      "claude-code",
      6000,
      "tool result",
      0.5,
      6,
      3000,
      "tool results are 50% of the window \u2014 the chat is NOT what's eating your context; 6 exact duplicate message(s), ~3000 tokens in repeated content; single message is 35% of the window"
    ],
    [
      4,
      "/fake/sessions/a2.jsonl",
      "codex",
      4000,
      "assistant",
      0.6,
      0,
      0,
      ""
    ]
  ],
  "row_count": 4
}
```
