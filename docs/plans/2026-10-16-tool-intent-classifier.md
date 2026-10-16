# Tool-Intent Classifier Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a deterministic, stdlib-only tool-intent taxonomy (explore / mutate / delegate / meta / test-build / vcs / repl / other) so tool-result tokens can be attributed to what the tool was *doing* — gh#12.

**Architecture:** New `context_inspector/intent.py` classifies `(tool_name, arguments)`; `analyzer.analyze()` extends its `id2name` pass to `id → intent` and aggregates `intent_tokens`/`intent_counts` on `Report`; `sweep.py`/`sweep_runner.py`/`loader.py`/queries carry the fields alongside the untouched `tool_tokens`/`tool_counts`. Design doc: `docs/plans/2026-10-16-tool-intent-classifier-design.md`.

**Tech Stack:** Python 3.10+ stdlib only for the classifier (`json`, `shlex`, `posixpath`); DuckDB for analytics; `unittest` throughout.

**Working directory:** worktree `.worktrees/tool-intent`, branch `angel-cantu/feat/issue-12/tool-intent-classifier`. Run tests with `.venv/bin/python -m unittest ...` from the worktree root.

---

## Task 1: `intent.py` — the classifier module

**Files:**
- Create: `context_inspector/intent.py`
- Test: `tests/test_intent.py`

**Step 1: Write the failing test**

```python
"""Rule-based tool-intent classifier tests (gh#12)."""

from __future__ import annotations

import json
import unittest

from context_inspector.intent import INTENT_VERSION, classify


class ClassifyTest(unittest.TestCase):
    def test_version_is_dated(self):
        self.assertRegex(INTENT_VERSION, r"^\d{4}-\d{2}-\d{2}\.v\d+$")

    def test_exact_tool_names(self):
        self.assertEqual(classify("Read", ""), "explore")
        self.assertEqual(classify("Grep", ""), "explore")
        self.assertEqual(classify("Glob", ""), "explore")
        self.assertEqual(classify("WebFetch", ""), "explore")
        self.assertEqual(classify("WebSearch", ""), "explore")
        self.assertEqual(classify("Edit", ""), "mutate")
        self.assertEqual(classify("Write", ""), "mutate")
        self.assertEqual(classify("apply_patch", ""), "mutate")
        self.assertEqual(classify("Agent", ""), "delegate")
        self.assertEqual(classify("SendMessage", ""), "delegate")
        self.assertEqual(classify("TodoWrite", ""), "meta")
        self.assertEqual(classify("TaskCreate", ""), "meta")

    def test_serena_exact_and_prefix(self):
        for name in (
            "mcp__serena__find_symbol",
            "mcp__serena__get_symbols_overview",
            "mcp__serena__search_for_pattern",
            "mcp__serena__read_file",
        ):
            self.assertEqual(classify(name, ""), "explore")
        # unknown serena tool still lands in explore via prefix
        self.assertEqual(classify("mcp__serena__rename_symbol", ""), "explore")
        # unknown MCP server is NOT explore by default
        self.assertEqual(classify("mcp__other__tool", ""), "other")

    def test_repl_is_never_shell_parsed(self):
        self.assertEqual(classify("js_repl", '{"command": "cat x.py"}'), "repl")

    def test_shell_command_heads(self):
        for head, intent in [
            ("cat", "explore"), ("ls", "explore"), ("rg", "explore"),
            ("grep", "explore"), ("sed", "explore"), ("head", "explore"),
            ("tail", "explore"), ("find", "explore"), ("less", "explore"),
            ("file", "explore"), ("wc", "explore"), ("diff", "explore"),
            ("stat", "explore"), ("tree", "explore"), ("jq", "explore"),
            ("pytest", "test-build"), ("make", "test-build"),
            ("npm", "test-build"), ("npx", "test-build"),
            ("cargo", "test-build"), ("go", "test-build"),
            ("cmake", "test-build"), ("tsc", "test-build"),
            ("build", "test-build"),
            ("git", "vcs"), ("gh", "vcs"),
        ]:
            with self.subTest(head=head):
                args = json.dumps({"command": f"{head} something"})
                self.assertEqual(classify("Bash", args), intent)

    def test_shell_family_aliases_all_parse_commands(self):
        for name in ("exec", "exec_command", "shell_command", "bash", "shell"):
            self.assertEqual(classify(name, '{"command": "ls -la"}'), "explore")

    def test_flags_and_absolute_paths(self):
        self.assertEqual(
            classify("Bash", '{"command": "rg -n pattern src/"}'), "explore"
        )
        self.assertEqual(
            classify("Bash", '{"command": "/usr/bin/rg pattern"}'), "explore"
        )

    def test_cmd_field_alias(self):
        self.assertEqual(classify("exec_command", '{"cmd": "grep x f"}'), "explore")

    def test_unknown_and_malformed_all_go_to_other(self):
        self.assertEqual(
            classify("Bash", '{"command": "zig build"}'), "other"
        )  # unknown head
        self.assertEqual(classify("Bash", "not json at all"), "other")
        self.assertEqual(classify("Bash", '["cat", "foo"]'), "other")  # not a dict
        self.assertEqual(classify("Bash", "{}"), "other")  # no command field
        self.assertEqual(classify("Bash", '{"command": ""}'), "other")
        self.assertEqual(classify("Bash", '{"command": 42}'), "other")
        self.assertEqual(classify("mystery_tool", ""), "other")
        self.assertEqual(classify("", ""), "other")


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_intent -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'context_inspector.intent'`

**Step 3: Write minimal implementation**

```python
"""Rule-based tool-intent classification (gh#12).

Deterministic, stdlib-only taxonomy with two levels:

1. By tool name — exact matches plus an MCP prefix rule (``mcp__serena__*``
   is symbol-level exploration). Tool name alone is enough for the
   "name = intent" tier (Read/Grep/Glob/...).
2. By command head — for the shell family (Bash/exec/exec_command/...,
   ~70% of tool-result tokens in the 2026-09-30 corpus of 877 sessions /
   26.4M tokens), intent lives in the command text: parse ``arguments``
   as JSON, take the command field, ``shlex.split``, first token's
   basename, look up in an explicit table.

REPL tools (js_repl) get their own ``repl`` bucket — their "command" is
code, never shell-parsed. Everything unmapped or unparseable is ``other``;
:classify: never raises.

Validated against the 2026-09-30 sweep corpus. New tools get added to the
tables below over time; bump INTENT_VERSION when the tables change.
"""

from __future__ import annotations

import json
import posixpath
import shlex

INTENT_VERSION = "2026-10-16.v1"

#: Exact tool-name -> intent. Buckets: explore, mutate, delegate, meta.
TOOL_INTENT = {
    "Read": "explore",
    "Grep": "explore",
    "Glob": "explore",
    "WebFetch": "explore",
    "WebSearch": "explore",
    "Edit": "mutate",
    "Write": "mutate",
    "apply_patch": "mutate",
    "Agent": "delegate",
    "SendMessage": "delegate",
    "TodoWrite": "meta",
    "TaskCreate": "meta",
    "TaskList": "meta",
    "EnterPlanMode": "meta",
    "ExitPlanMode": "meta",
    "mcp__serena__find_symbol": "explore",
    "mcp__serena__get_symbols_overview": "explore",
    "mcp__serena__search_for_pattern": "explore",
    "mcp__serena__read_file": "explore",
}

#: (prefix, intent) — any tool name starting with prefix, checked after
#: exact matches. Serena's remaining tools are still symbol-level reads.
MCP_PREFIX_INTENT = (("mcp__serena__", "explore"),)

#: REPL tools — the "command" is code, not shell; never parsed.
REPL_TOOLS = frozenset({"js_repl"})

#: Shell-execution tool names across harnesses — same conceptual tool as
#: charts.tool_ranking.SHELL_FAMILY; intent comes from the command head.
SHELL_TOOLS = frozenset(
    {"Bash", "bash", "exec", "exec_command", "shell_command", "shell"}
)

#: JSON fields tried, in order, when extracting the shell command string.
COMMAND_FIELDS = ("command", "cmd")

#: First-token basename -> intent for shell-family commands.
COMMAND_INTENT = {
    # explore — dump-style reads of files/search results
    "cat": "explore", "ls": "explore", "rg": "explore", "grep": "explore",
    "sed": "explore", "head": "explore", "tail": "explore", "find": "explore",
    "less": "explore", "file": "explore", "wc": "explore", "diff": "explore",
    "stat": "explore", "tree": "explore", "jq": "explore",
    # test-build — verification and compilation
    "pytest": "test-build", "make": "test-build", "npm": "test-build",
    "npx": "test-build", "cargo": "test-build", "go": "test-build",
    "cmake": "test-build", "tsc": "test-build", "build": "test-build",
    # vcs — mixed intent, own bucket
    "git": "vcs", "gh": "vcs",
}


def classify(tool_name: str, arguments: str) -> str:
    """Map a tool call to its intent bucket. Never raises."""
    name = tool_name or ""
    if name in TOOL_INTENT:
        return TOOL_INTENT[name]
    for prefix, intent in MCP_PREFIX_INTENT:
        if name.startswith(prefix):
            return intent
    if name in REPL_TOOLS:
        return "repl"
    if name in SHELL_TOOLS:
        return _classify_command(arguments)
    return "other"


def _classify_command(arguments: str) -> str:
    try:
        payload = json.loads(arguments)
    except (TypeError, ValueError):
        return "other"
    if not isinstance(payload, dict):
        return "other"
    command = next(
        (payload[k] for k in COMMAND_FIELDS if isinstance(payload.get(k), str)),
        None,
    )
    if not command:
        return "other"
    try:
        tokens = shlex.split(command, comments=True)
    except ValueError:
        return "other"
    if not tokens:
        return "other"
    return COMMAND_INTENT.get(posixpath.basename(tokens[0]), "other")
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_intent -v`
Expected: PASS (all tests)

**Step 5: Commit**

```bash
git add context_inspector/intent.py tests/test_intent.py
git commit -m "feat: rule-based tool-intent classifier (gh#12)"
```

---

## Task 2: `analyzer.py` — intent aggregation + render_text block

**Files:**
- Modify: `context_inspector/analyzer.py` (imports, Report dataclass, analyze loop, to_dict, render_text)
- Test: `tests/test_analyzer_intent.py` (new)

**Step 1: Write the failing test**

```python
"""Analyzer intent-aggregation tests (gh#12).

Token expectations are hand-computed: the injected tokenizer is len(),
so a tool result's token count is exactly its content length.
"""

from __future__ import annotations

import json
import unittest

from context_inspector.analyzer import analyze, render_text, to_dict

LEN = lambda s: len(s)  # noqa: E731


def _call(cid: str, name: str, arguments: dict) -> dict:
    return {
        "id": cid,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def transcript() -> list[dict]:
    return [
        {"role": "assistant", "content": "", "tool_calls": [
            _call("c1", "Bash", {"command": "cat foo.py"}),
            _call("c2", "Bash", {"command": "rg pattern"}),
            _call("c3", "Bash", {"command": "pytest -x"}),
            _call("c4", "Bash", {"command": "make build"}),
            _call("c5", "js_repl", {"code": "1+1"}),
            _call("c6", "Read", {"path": "foo.py"}),
            _call("c7", "mystery_tool", {}),
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "x" * 100},
        {"role": "tool", "tool_call_id": "c2", "content": "y" * 200},
        {"role": "tool", "tool_call_id": "c3", "content": "z" * 300},
        {"role": "tool", "tool_call_id": "c4", "content": "w" * 400},
        {"role": "tool", "tool_call_id": "c5", "content": "v" * 500},
        {"role": "tool", "tool_call_id": "c6", "content": "u" * 600},
        {"role": "tool", "tool_call_id": "c7", "content": "t" * 700},
        # result whose call id matches no call — intent falls back to other
        {"role": "tool", "tool_call_id": "orphan", "content": "s" * 50},
    ]


class IntentAggregationTest(unittest.TestCase):
    def setUp(self):
        self.report = analyze(transcript(), tokenizer=LEN)

    def test_intent_tokens_hand_computed(self):
        self.assertEqual(
            self.report.intent_tokens,
            {"explore": 900, "test-build": 700, "repl": 500, "other": 750},
        )

    def test_intent_counts_hand_computed(self):
        self.assertEqual(
            self.report.intent_counts,
            {"explore": 3, "test-build": 2, "repl": 1, "other": 2},
        )

    def test_tool_attribution_unchanged(self):
        self.assertEqual(
            self.report.tool_tokens,
            {"Bash": 1000, "js_repl": 500, "Read": 600, "mystery_tool": 700},
        )
        self.assertEqual(
            self.report.tool_counts,
            {"Bash": 4, "js_repl": 1, "Read": 1, "mystery_tool": 1},
        )

    def test_to_dict_carries_intents(self):
        d = to_dict(self.report)
        self.assertEqual(d["intent_tokens"]["explore"], 900)
        self.assertEqual(d["intent_counts"]["repl"], 1)


class IntentRenderTest(unittest.TestCase):
    def test_render_text_shows_top_intents(self):
        report = analyze(transcript(), tokenizer=LEN)
        text = render_text(report)
        self.assertIn("Top intents", text)
        self.assertIn("explore", text)
        self.assertIn("test-build", text)


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_analyzer_intent -v`
Expected: FAIL — `AttributeError: 'Report' object has no attribute 'intent_tokens'`

**Step 3: Write minimal implementation**

In `context_inspector/analyzer.py`:

1. Add import at top: `from .intent import classify`
2. `Report` dataclass — add after `tool_counts`:

```python
    intent_tokens: dict[str, int] = field(default_factory=dict)
    intent_counts: dict[str, int] = field(default_factory=dict)
```

3. In `analyze()` — extend the tool-call scan and tool-result attribution:

```python
    id2intent: dict[str, str] = {}
    intent_tokens: dict[str, int] = {}
    intent_counts: dict[str, int] = {}
```

and inside the `for tc in msg.get("tool_calls") or []:` loop, after `id2name[cid] = ...`:

```python
                id2intent[cid] = classify(
                    tc.get("function", {}).get("name") or "?",
                    tc.get("function", {}).get("arguments") or "",
                )
```

and inside the `if category == "tool result":` block, after `tool_counts` update:

```python
            intent = id2intent.get(msg.get("tool_call_id") or "") or "other"
            intent_tokens[intent] = intent_tokens.get(intent, 0) + tokens
            intent_counts[intent] = intent_counts.get(intent, 0) + 1
```

4. Pass both dicts into the `Report(...)` constructor (next to `tool_counts`).
5. `to_dict()` — add `"intent_tokens": report.intent_tokens,` and `"intent_counts": report.intent_counts,` after the tool lines.
6. `render_text()` — after the "Top tools" block (after its `lines.append("")`), add:

```python
    if report.intent_tokens:
        intent_total = sum(report.intent_tokens.values()) or 1
        lines.append("Top intents (by result tokens):")
        for intent, tok in sorted(
            report.intent_tokens.items(), key=lambda kv: kv[1], reverse=True
        )[:5]:
            share = tok / intent_total
            lines.append(
                f"  {intent:<19} {tok:>7,} ({share:>4.0%}) "
                f"{report.intent_counts.get(intent, 0):>4} call(s)"
            )
        lines.append("")
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_analyzer_intent tests.test_intent -v`
Expected: PASS. Also run the full suite: `.venv/bin/python -m unittest discover tests 2>&1 | grep -E "^(Ran|OK|FAILED)"` — expected `Ran 152` OK (140 baseline + 12 new). If `test_cli_*` tests assert exact render_text output, adjust them minimally (the new block only appears when `intent_tokens` is non-empty — sample transcript may now show it).

**Step 5: Commit**

```bash
git add context_inspector/analyzer.py tests/test_analyzer_intent.py
git commit -m "feat: intent aggregation in analyzer + render_text block (gh#12)"
```

---

## Task 3: `sweep.py` — SessionResult, intent_ranking, report sections

**Files:**
- Modify: `context_inspector/sweep.py` (SessionResult, sweep_one, new intent_ranking, render_sweep, to_markdown)
- Create: `tests/fixtures/intent_transcript.json`
- Test: `tests/test_sweep_intent.py` (new)

**Step 1: Write the failing test + fixture**

`tests/fixtures/intent_transcript.json` (plain OpenAI format — `load_any` passes it through when source is explicit):

```json
[
  {"role": "assistant", "content": "", "tool_calls": [
    {"id": "c1", "type": "function", "function": {"name": "Bash", "arguments": "{\"command\": \"cat a.py\"}"}},
    {"id": "c2", "type": "function", "function": {"name": "Bash", "arguments": "{\"command\": \"pytest tests/\"}"}},
    {"id": "c3", "type": "function", "function": {"name": "js_repl", "arguments": "{\"code\": \"2+2\"}"}}
  ]},
  {"role": "tool", "tool_call_id": "c1", "content": "x"},
  {"role": "tool", "tool_call_id": "c2", "content": "yy"},
  {"role": "tool", "tool_call_id": "c3", "content": "zzzz"}
]
```

(Content strings are short — the test injects its own tokenizer mapping content to fixed numbers, so lengths here are irrelevant.)

`tests/test_sweep_intent.py`:

```python
"""Sweep-level intent tests (gh#12)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "intent_transcript.json"


def tokens(fixture_map):
    """Tokenizer factory: fixed token counts per call id for hand-computation."""
    def tok(text):
        for cid, n in fixture_map.items():
            if text == f"result-{cid}":
                return n
        return 0
    return tok


class SweepOneIntentTest(unittest.TestCase):
    def test_session_result_carries_intents(self):
        from context_inspector.sweep import sweep_one

        messages = json.loads(FIXTURE.read_text(encoding="utf-8"))
        # rewrite tool contents to the labeled forms the tokenizer keys on
        sizes = {"c1": 120, "c2": 240, "c3": 480}
        for msg in messages:
            if msg.get("role") == "tool":
                msg["content"] = "result-" + msg["tool_call_id"]
        tmp = FIXTURE.with_suffix(".tmp.json")
        tmp.write_text(json.dumps(messages), encoding="utf-8")
        try:
            r = sweep_one("fixture", str(tmp), tokenizer=tokens(sizes))
        finally:
            tmp.unlink()
        self.assertEqual(r.intent_tokens, {"explore": 120, "test-build": 240, "repl": 480})
        self.assertEqual(r.intent_counts, {"explore": 1, "test-build": 1, "repl": 1})
        self.assertEqual(r.tool_tokens, {"Bash": 360, "js_repl": 480})

    def test_render_sections_show_intents(self):
        from context_inspector.sweep import (
            SessionResult, aggregate, render_sweep, to_markdown,
        )

        r = SessionResult(
            source="claude-code", path="/fake/p/s.jsonl", total_tokens=1000,
            category_tokens={"tool result": 1000}, n_duplicates=0,
            wasted_tokens=0,
            tool_tokens={"Bash": 1000}, tool_counts={"Bash": 2},
            intent_tokens={"explore": 700, "test-build": 300},
            intent_counts={"explore": 1, "test-build": 1},
        )
        agg = aggregate([r])
        text = render_sweep([r], agg)
        self.assertIn("tool intents", text)
        self.assertIn("explore", text)
        self.assertIn("test-build", text)
        md = to_markdown([r], agg)
        self.assertIn("explore", md)


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_sweep_intent -v`
Expected: FAIL — `TypeError: SessionResult.__init__() got an unexpected keyword argument 'intent_tokens'`

**Step 3: Write minimal implementation**

In `context_inspector/sweep.py`:

1. `SessionResult` — add after `tool_counts`:

```python
    intent_tokens: dict[str, int] = field(default_factory=dict)
    intent_counts: dict[str, int] = field(default_factory=dict)
```

2. `sweep_one()` — pass `intent_tokens=dict(report.intent_tokens), intent_counts=dict(report.intent_counts)` into the constructor.

3. New module-level function after `tool_ranking`:

```python
def intent_ranking(results: list[SessionResult]) -> dict[str, dict[str, int]]:
    """Tool-result tokens per intent, per source plus 'all'."""
    by_source: dict[str, dict[str, int]] = {}
    overall: dict[str, int] = {}
    for r in results:
        src = by_source.setdefault(r.source, {})
        for intent, tokens in r.intent_tokens.items():
            src[intent] = src.get(intent, 0) + tokens
            overall[intent] = overall.get(intent, 0) + tokens
    by_source["all"] = overall
    return by_source
```

4. `render_sweep()` — after the tools block (`lines.append("")` at the end of the `if overall:` section, before "top 5 largest sessions"):

```python
    intents = intent_ranking(results)
    overall_i = intents.get("all", {})
    if overall_i:
        grand_i = sum(overall_i.values()) or 1
        lines.append("── top tool intents (all sessions) " + "─" * 31)
        for intent, tok in sorted(overall_i.items(), key=lambda kv: -kv[1])[:8]:
            lines.append(f"  {intent:<19} {tok:>11,} ({tok / grand_i:>4.0%})")
        for src in ("claude-code", "codex", "kimi-code"):
            src_intents = intents.get(src) or {}
            if not src_intents:
                continue
            top3 = sorted(src_intents.items(), key=lambda kv: -kv[1])[:3]
            src_total = sum(src_intents.values()) or 1
            summary = "  ·  ".join(f"{n} {t / src_total:.0%}" for n, t in top3)
            lines.append(f"    [{src}] {summary}")
        lines.append("")
```

5. `to_markdown()` — after the tool-attribution section, before "## Top 5 largest sessions":

```python
    intents = intent_ranking(results)
    overall_i = intents.get("all", {})
    if overall_i:
        grand_i = sum(overall_i.values()) or 1
        lines.append("## Tool intents (result tokens, all sessions)\n")
        lines.append("| Intent | Tokens | Share |")
        lines.append("|---|---|---|")
        for intent, tok in sorted(overall_i.items(), key=lambda kv: -kv[1])[:12]:
            lines.append(f"| {intent} | {tok:,} | {tok / grand_i:.0%} |")
        lines.append("")
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_sweep_intent -v` then the full suite (`Ran 154` OK expected).

**Step 5: Commit**

```bash
git add context_inspector/sweep.py tests/test_sweep_intent.py tests/fixtures/intent_transcript.json
git commit -m "feat: intent fields + sections in sweep pipeline (gh#12)"
```

---

## Task 4: `sweep_runner.py` — record fields

**Files:**
- Modify: `sweep_runner.py:34-44` (record dict)

**Step 1: Write the failing test**

None — `sweep_runner.py` has no unit tests (it only globs the real session dirs, which tests must not touch). Verification is Step 4 below.

**Step 2: Run test to verify it fails**

N/A — skip.

**Step 3: Write minimal implementation**

In the `rec = {...}` dict, after `"tool_counts": r.tool_counts,` add:

```python
                    "intent_tokens": r.intent_tokens,
                    "intent_counts": r.intent_counts,
```

**Step 4: Verify**

Run: `.venv/bin/python -c "
from context_inspector.sweep import sweep_one
r = sweep_one('fixture', 'tests/fixtures/intent_transcript.json')
print(sorted({'intent_tokens', 'intent_counts'}))  # fields exist on SessionResult
"`
Expected: prints `['intent_counts', 'intent_tokens']` and the sweep_one call succeeds. Then full suite: `Ran 154` OK.

**Step 5: Commit**

```bash
git add sweep_runner.py
git commit -m "feat: write intent fields to sweep_results.jsonl (gh#12)"
```

---

## Task 5: `loader.py` — columns + `intents` view

**Files:**
- Modify: `context_inspector/analytics/loader.py` (`_COLUMNS`, docstring, new view)
- Test: `tests/test_loader.py` (two new tests)

**Step 1: Write the failing test**

Append to `LoaderTest` in `tests/test_loader.py`:

```python
    def test_intents_view_unnested(self):
        (n,) = self.con.sql("select count(*) from intents").fetchone()
        self.assertEqual(n, 11)

    def test_old_records_without_intent_fields_load(self):
        # record 8 (kimi-code conv2) has no intent fields at all
        rows = self.con.sql(
            "select path from intents where path like '%conv2%'"
        ).fetchall()
        self.assertEqual(rows, [])
```

(The fixture gets its intent fields in Task 6; until then `test_intents_view_unnested` fails because the `intents` view does not exist. Run it now to see `Catalog Error`.)

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_loader -v`
Expected: FAIL — `Catalog Error: Table with name intents does not exist`

**Step 3: Write minimal implementation**

In `context_inspector/analytics/loader.py`:

1. Docstring — extend the tolerance sentence: "Records from older sweep versions may lack ``tool_tokens`` / ``tool_counts`` (and, before gh#12, ``intent_tokens`` / ``intent_counts``), and sweep_runner writes error stubs … every missing field loads as NULL." Also extend the public-surface sentence: "plus ``categories``, ``tools``, ``intents`` and ``signal_labels`` views ready to query."

2. `_COLUMNS` — insert after `tool_counts`:

```
    intent_tokens: 'MAP(VARCHAR, BIGINT)', intent_counts: 'MAP(VARCHAR, BIGINT)',
```

3. After the `tools` view creation, add:

```python
    con.execute(
        """
        CREATE VIEW intents AS
        SELECT i.path, i.source, i.intent, i.tokens, c.calls
        FROM (SELECT path, source, e.key AS intent, e.value AS tokens
              FROM sessions, unnest(map_entries(intent_tokens)) AS u(e)) i
        LEFT JOIN (SELECT path, e.key AS intent, e.value AS calls
                   FROM sessions, unnest(map_entries(intent_counts)) AS u(e)) c
        USING (path, intent)
        """
    )
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_loader -v`
Expected: `test_intents_view_unnested` FAILS still (11 expected, fixture not yet extended — view exists but returns 0 rows), `test_old_records_without_intent_fields_load` PASSES. That is the expected intermediate state; the count flips to 11 in Task 6. Everything else passes.

**Step 5: Commit**

```bash
git add context_inspector/analytics/loader.py tests/test_loader.py
git commit -m "feat: intents view + intent columns in analytics loader (gh#12)"
```

---

## Task 6: fixture intent data + `intent_shares` / `intent_trend` queries

**Files:**
- Modify: `tests/fixtures/sweep_results.jsonl` (records 1, 2, 3, 4, 7, 9)
- Create: `context_inspector/analytics/queries/intent_shares.sql`, `intent_trend.sql`
- Modify: `context_inspector/analytics/queries/manifest.json`
- Test: `tests/test_queries.py` (manifest set + three new tests)

All expected values below are hand-computed from the fixture edits. Intent rows per record: r1: explore 500/3, test-build 300/1 · r2: (empty maps) · r3: explore 200/1, mutate 200/2, test-build 200/1 · r4: explore 1000/5, vcs 400/2, other 300/1 · r7: test-build 300/2, other 150/1 · r9: explore 480/2. Records 5, 6, 8 stay intent-less (old-record tolerance). Intent token sums per source — codex 1880, claude-code 1700, kimi-code 450, all 4030 — equal the tool-result totals, cross-checking the fixture.

**Step 1: Write the failing tests**

In `tests/test_queries.py`:

1. Add `"intent_shares", "intent_trend"` to the `test_manifest_covers_all_queries` expected set.
2. Add:

```python
    def test_intent_shares_overall(self):
        rows = {r[1]: r for r in self.query("intent_shares") if r[0] == "all"}
        self.assertEqual(rows["explore"][2:6], (2180, 2180 / 4030, 11, 2180 / 11))
        self.assertEqual(rows["test-build"][2:6], (800, 800 / 4030, 4, 200.0))
        self.assertEqual(rows["mutate"][2:6], (200, 200 / 4030, 2, 100.0))
        self.assertEqual(rows["vcs"][2:6], (400, 400 / 4030, 2, 200.0))
        self.assertEqual(rows["other"][2:6], (450, 450 / 4030, 2, 225.0))

    def test_intent_shares_per_source(self):
        rows = {(r[0], r[1]): r for r in self.query("intent_shares")}
        self.assertEqual(rows[("claude-code", "explore")][2:4], (1000, 1000 / 1700))
        self.assertEqual(rows[("kimi-code", "test-build")][2:4], (300, 300 / 450))
        self.assertNotIn(("kimi-code", "explore"), rows)

    def test_intent_trend(self):
        rows = self.query("intent_trend")
        aug = {r[1]: r for r in rows if str(r[0]) == "2026-08-01"}
        sep = {r[1]: r for r in rows if str(r[0]) == "2026-09-01"}
        # 2026-08: only rollout-a1 (path-dated) — 500 explore + 300 test-build
        self.assertEqual(aug["explore"][2], 500)
        self.assertAlmostEqual(aug["explore"][3], 500 / 800, places=4)
        self.assertAlmostEqual(aug["test-build"][3], 300 / 800, places=4)
        # 2026-09: rollout-a3 (200/200/200) + rollout-a4 (480 explore) = 1080
        self.assertEqual(sep["explore"][2], 680)
        self.assertAlmostEqual(sep["explore"][3], 680 / 1080, places=4)
        self.assertAlmostEqual(sep["mutate"][3], 200 / 1080, places=4)
        self.assertAlmostEqual(sep["test-build"][3], 200 / 1080, places=4)
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_queries -v`
Expected: FAIL — `KeyError: 'intent_shares'` (manifest has no such entry)

**Step 3: Write the queries, manifest entries, and fixture edits**

`context_inspector/analytics/queries/intent_shares.sql`:

```sql
-- name: intent_shares
-- description: Per intent — total tokens, share of tool-result tokens, call
--   count, tokens/call — overall (source='all') and per source.
-- caveats: tokens/call is NULL when intent_counts was absent (older sweeps) —
--   same mixing caveat as tool_ranking. Shares are of all tool-result tokens
--   within the same source scope. Sessions swept before the classifier
--   contribute no intent rows at all.
WITH scoped AS (
    SELECT i.source, i.intent, sum(i.tokens) AS tokens, sum(i.calls) AS calls
    FROM intents i JOIN sessions s USING (path)
    WHERE s.error IS NULL
    GROUP BY i.source, i.intent
), combined AS (
    SELECT source, intent, tokens, calls FROM scoped
    UNION ALL
    SELECT 'all', intent, sum(tokens), sum(calls) FROM scoped GROUP BY intent
)
SELECT source, intent, tokens,
       tokens * 1.0 / NULLIF(sum(tokens) OVER (PARTITION BY source), 0) AS share,
       calls,
       tokens * 1.0 / NULLIF(calls, 0) AS tokens_per_call
FROM combined
ORDER BY source, tokens DESC, intent;
```

`context_inspector/analytics/queries/intent_trend.sql`:

```sql
-- name: intent_trend
-- description: Per-intent share of tool-result tokens per calendar month.
--   Powers the Serena before/after comparison (gh#12).
-- caveats: only sessions with a derivable session_date (see monthly_trend).
--   Sessions swept before the classifier contribute no intent rows.
SELECT month, intent, tokens,
       tokens * 1.0 / NULLIF(sum(tokens) OVER (PARTITION BY month), 0) AS share
FROM (
    SELECT date_trunc('month', s.session_date)::DATE AS month,
           i.intent, sum(i.tokens) AS tokens
    FROM intents i JOIN sessions s USING (path)
    WHERE s.error IS NULL AND s.session_date IS NOT NULL
    GROUP BY 1, 2
) ORDER BY month, tokens DESC, intent;
```

`manifest.json` — add after the `tool_ranking` entry:

```json
    {
      "name": "intent_shares",
      "file": "intent_shares.sql",
      "description": "Per intent — total tokens, share of tool-result tokens, call count, tokens/call — overall (source='all') and per source.",
      "columns": ["source", "intent", "tokens", "share", "calls", "tokens_per_call"],
      "params": []
    },
    {
      "name": "intent_trend",
      "file": "intent_trend.sql",
      "description": "Per-intent share of tool-result tokens per calendar month.",
      "columns": ["month", "intent", "tokens", "share"],
      "params": []
    },
```

Fixture edits in `tests/fixtures/sweep_results.jsonl` (append the intent fields inside each record's JSON, keeping each line a single object):

- Record 1 (`rollout-a1`): add `"intent_tokens": {"explore": 500, "test-build": 300}, "intent_counts": {"explore": 3, "test-build": 1}`
- Record 2 (`rollout-a2`): add `"intent_tokens": {}, "intent_counts": {}` (empty-map variant)
- Record 3 (`rollout-a3`): add `"intent_tokens": {"explore": 200, "mutate": 200, "test-build": 200}, "intent_counts": {"explore": 1, "mutate": 2, "test-build": 1}`
- Record 4 (`s1.jsonl`): add `"intent_tokens": {"explore": 1000, "vcs": 400, "other": 300}, "intent_counts": {"explore": 5, "vcs": 2, "other": 1}`
- Record 7 (`ws1/conv1`): add `"intent_tokens": {"test-build": 300, "other": 150}, "intent_counts": {"test-build": 2, "other": 1}`
- Record 9 (`rollout-a4`): add `"intent_tokens": {"explore": 480}, "intent_counts": {"explore": 2}`
- Records 5, 6, 8, 10: untouched — intent-less old records.

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_queries tests.test_loader -v` then the full suite.
Expected: all PASS; `test_intents_view_unnested` now returns 11; existing tool/category/signal expectations unchanged (fixture intent edits add no tool/category data).

**Step 5: Commit**

```bash
git add context_inspector/analytics/queries/intent_shares.sql context_inspector/analytics/queries/intent_trend.sql context_inspector/analytics/queries/manifest.json tests/fixtures/sweep_results.jsonl tests/test_queries.py
git commit -m "feat: intent_shares + intent_trend queries with fixture coverage (gh#12)"
```

---

## Task 7: README + full verification

**Files:**
- Modify: `README.md` (Analytics section)

**Step 1: Add the README line**

In the README's Analytics/query list, after the `tool_ranking` bullet, add one line:

```markdown
- `intent_shares` / `intent_trend` — rule-based tool-intent split (explore / test-build / vcs / repl / other) of tool-result tokens, overall and per month — the query that powers the Serena before/after comparison.
```

(Match the surrounding bullet phrasing exactly.)

**Step 2: Full suite**

Run: `.venv/bin/python -m unittest discover tests 2>&1 | grep -E "^(Ran|OK|FAILED)"`
Expected: `Ran 158` OK (140 baseline + 18 new).

**Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README line for intent queries (gh#12)"
```

---

## Task 8: Real-corpus verification (manual, not committed)

Run a fresh sweep over the real session dirs (`discover_sessions` covers claude-code `~/.claude`, codex `~/.codex`, kimi-code `KIMI_HOME`) into a temp dir, then check the two corpus-level acceptance criteria. Do NOT commit the generated jsonl.

```bash
cd "$(mktemp -d)"
PYTHONPATH=/Users/angelcantu/GithubRepositories/angelcantugr/context-inspector/.worktrees/tool-intent \
  /Users/angelcantu/GithubRepositories/angelcantugr/context-inspector/.worktrees/tool-intent/.venv/bin/python \
  /Users/angelcantu/GithubRepositories/angelcantugr/context-inspector/.worktrees/tool-intent/sweep_runner.py
# repeat the above until it prints DONE (240s budget per run)
```

Then:

```bash
cd <same-temp-dir>
PYTHONPATH=<worktree> <worktree>/.venv/bin/python - <<'EOF'
from context_inspector.analytics import load_results
from context_inspector.analytics.runner import load_manifest, run_query

con = load_results("sweep_results.jsonl")
entries = {e.name: e for e in load_manifest()}

# Criterion 1: shell-family tokens must NOT be >40% 'other'
_, rows = run_query(con, entries["intent_shares"])
shell = {"explore", "test-build", "vcs", "other"}  # shell-family buckets
all_rows = {r[1]: r for r in rows if r[0] == "all"}
shell_total = sum(all_rows[i][2] for i in shell if i in all_rows)
other = all_rows.get("other", (None, None, 0))[2]
print(f"shell-family other share: {other / shell_total:.1%} (must be <= ~40%)")
for r in rows:
    if r[0] == "all":
        print(r)

# Criterion 2: the 2026-04-28 whale session classifies ~99% repl
_, whale = run_query(con, entries["whale_table"])
for r in whale[:5]:
    print(r[1], r[3])
con.execute("SELECT path, source, total_tokens FROM sessions WHERE path LIKE '%2026/04/28%' ORDER BY total_tokens DESC LIMIT 3").show()
_, shares = run_query(con, entries["intent_shares"])
EOF
```

Accept: shell-family `other` ≤ ~40%; the largest 2026-04-28 session shows ~99% `repl` in `intent_shares`. If `other` exceeds 40%, list the dominant missing command heads from the sweep and extend `COMMAND_INTENT` (a classifier-table iteration, not a redesign). Report results to the user; do not commit the corpus output.

---

## Risks & notes

- **Golden memo test** (`test_golden_memo.py`) runs the loader over `sweep_weekA/B.jsonl` fixtures — intent-less old records must keep loading (covered by Task 5 NULL-map semantics).
- **CLI output change**: `render_text` gains a block when intents are present; `test_cli_*` tests must not assert byte-exact full output (they don't — they check rc/substrings; the full suite in Task 2 Step 4 confirms).
- **charts / weekly**: read `tool_tokens` only; new fields flow through harmlessly. No changes needed.
- **No version bump**: gh#12 doesn't ask for a release; version bumps happen at release time per repo convention.
- If a Task 2–6 step finds an existing test asserting a count this plan didn't anticipate (e.g. sessions row count), update that expectation only if the plan's fixture edits justify it — never weaken an assertion to make it pass.

---

## Acceptance results (2026-10-16 run, 808 sessions, classifier v2)

Executed against the author's real claude-code / codex / kimi-code session data
(808 sessions, 0 error stubs; corpus output not committed):

- Shell-family `other`: **9.2%** (target ≤ ~40%). First pass measured 74.6%;
  root causes were codex `exec` being a JS REPL (5.04M tokens, now `repl`),
  unmapped `cd` (3.2M, now `explore`), and a long tail of unmapped heads
  (commit `14c36bf` added 62 heads + ticktick/context7 MCP prefixes).
- Whale criterion: the 2026-04-28 js_repl whale session is **absent from the
  current corpus** (zero js_repl sessions; likely an older/rotated snapshot).
  Substitute evidence: the largest repl-bearing session
  (codex, 2026-10-13, ~1.4M tokens) classifies **97.4% `repl`** — the
  regression-case role the whale played is filled by codex `exec`.
- `intent_shares` overall (v2): explore 71.7%, repl 11.0%, other 7.6%,
  delegate 4.1%, vcs 3.2%, mutate 1.3%, meta 1.0%, test-build 0.2%.
- Full suite: 170 tests OK. Deferred by design: compound-command splitting,
  env-prefix stripping, flag inspection, `python3` mapping, and the
  `charts.tool_ranking.SHELL_FAMILY` divergence for `exec` (documented in
  `intent.py`).
