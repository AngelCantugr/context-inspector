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
