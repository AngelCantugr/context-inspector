"""Inspect-only tool surface for the interpretation agent (#15).

Hard-coded tools — the agent reads evidence, it never mutates anything:

- ``analyze_session(path)``          full token breakdown of one session log
- ``run_query(name)``                one query-pack SQL entry vs the snapshot
- ``read_window(path, start_line, n)``  bounded peek at raw transcript lines

Every tool return is registered in the ``ToolContext`` citation registry
with an id (T1, T2, ...) and the id is embedded in the text the model
sees, so memo findings can bind citations to actual tool returns.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..adapters import load_any
from ..analyzer import analyze

MAX_WINDOW_LINES = 50  # read_window returns at most this many lines
_CONTENT_LIMIT = 4000  # chars of a tool return kept in the citation registry


@dataclass
class ToolReturn:
    """One executed tool call, registered as a citable evidence record.

    ``ok`` distinguishes successful evidence from error results (missing
    files, exhausted budgets, unknown tools) — the memo citation gate
    only accepts citations bound to successful returns.
    """

    id: str
    tool: str
    arguments: dict
    content: str  # JSON text, truncated to _CONTENT_LIMIT chars
    ok: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tool": self.tool,
            "arguments": self.arguments,
            "content": self.content,
            "ok": self.ok,
        }


@dataclass
class ToolContext:
    """Per-run tool state: where the evidence lives and what it returned.

    ``reads_used`` tracks read_window calls for the current outlier; the
    graph resets it to 0 before each deep dive.

    ``allowed_paths`` is the set of authorized session files (resolved at
    build time from the manifest and snapshot). The file tools refuse any
    model-supplied path that does not resolve — after expanduser, ``..``
    normalization and symlink resolution — to exactly one member. An
    empty set denies every file read (deny by default).
    """

    root: Path
    snapshot_path: Path
    max_reads_per_outlier: int = 3
    reads_used: int = 0
    citations: list[ToolReturn] = field(default_factory=list)
    allowed_paths: set[Path] = field(default_factory=set)

    def authorize(self, path: str) -> Path | None:
        """Resolve ``path``; return it only if it is an authorized session file."""

        if not path:
            return None
        try:
            resolved = Path(path).expanduser().resolve()
        except OSError:
            return None
        return resolved if resolved in self.allowed_paths else None


def _analyze_session(args: dict, ctx: ToolContext) -> dict:
    path = args.get("path", "")
    target = ctx.authorize(path)
    if target is None:
        return {"error": f"path is not an authorized session file: {path!r}"}
    try:
        messages, source = load_any(target)
        report = analyze(messages)
    except Exception as exc:
        return {"error": f"analyze_session failed for {path}: {exc}"}
    top_tools = sorted(report.tool_tokens.items(), key=lambda kv: -kv[1])[:5]
    top_msgs = sorted(report.messages, key=lambda m: -m.tokens)[:5]
    return {
        "path": str(target),
        "source": source,
        "total_tokens": report.total_tokens,
        "category_tokens": report.category_tokens,
        "signals": report.signals,
        "n_duplicates": len(report.duplicates),
        "top_tools": [
            {"tool": name, "tokens": tokens, "calls": report.tool_counts.get(name, 0)}
            for name, tokens in top_tools
        ],
        "largest_messages": [
            {
                "index": m.index,
                "role": m.role,
                "category": m.category,
                "tokens": m.tokens,
            }
            for m in top_msgs
        ],
    }


def _run_query(args: dict, ctx: ToolContext) -> dict:
    name = args.get("name", "")
    try:
        import duckdb  # noqa: F401  (presence check; loader imports its own)
    except ImportError:
        raise RuntimeError(
            "run_query needs duckdb — install the agent extra with "
            '`uv sync --extra agent` (or `pip install "context-inspector[agent]"`)'
        ) from None
    from ..analytics.loader import load_results
    from ..analytics.runner import load_manifest, run_query as run_pack_query

    entries = {e.name: e for e in load_manifest()}
    if name not in entries:
        return {
            "error": f"unknown query {name!r} (available: {', '.join(sorted(entries))})"
        }
    con = load_results(str(ctx.snapshot_path))
    columns, rows = run_pack_query(con, entries[name])
    return {
        "query": name,
        "description": entries[name].description,
        "columns": columns,
        "rows": [list(row) for row in rows[:50]],
        "row_count": len(rows),
    }


def _read_window(args: dict, ctx: ToolContext) -> dict:
    path = args.get("path", "")
    target = ctx.authorize(path)
    if target is None:
        return {"error": f"path is not an authorized session file: {path!r}"}
    if ctx.reads_used >= ctx.max_reads_per_outlier:
        return {
            "error": (
                f"read_window budget exhausted for this deep dive "
                f"({ctx.max_reads_per_outlier} reads) — rely on analyze_session "
                "and run_query evidence instead"
            )
        }
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return {"error": f"read_window failed for {path}: {exc}"}
    ctx.reads_used += 1
    start = max(0, int(args.get("start_line", 0) or 0))
    n = min(max(1, int(args.get("n", MAX_WINDOW_LINES) or MAX_WINDOW_LINES)), MAX_WINDOW_LINES)
    window = lines[start : start + n]
    return {
        "path": str(target),
        "start_line": start,
        "lines": [
            {"line": start + i + 1, "text": line[:500]}
            for i, line in enumerate(window)
        ],
        "total_lines": len(lines),
        "truncated": start + n < len(lines),
    }


_TOOLS: dict[str, Callable[[dict, ToolContext], dict]] = {
    "analyze_session": _analyze_session,
    "run_query": _run_query,
    "read_window": _read_window,
}

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "analyze_session",
        "description": (
            "Token breakdown of one session log: category shares, biggest "
            "tool-result producers, bloat signals, largest messages."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "session log path"}},
            "required": ["path"],
        },
    },
    {
        "name": "run_query",
        "description": (
            "Run one named query from the analytics query pack against this "
            "week's snapshot; returns columns and rows as JSON."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "query name"}},
            "required": ["name"],
        },
    },
    {
        "name": "read_window",
        "description": (
            "Bounded peek at raw transcript lines (at most 50 lines per call; "
            "limited calls per deep dive)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer"},
                "n": {"type": "integer"},
            },
            "required": ["path"],
        },
    },
]


def execute_tool(name: str, arguments: dict | None, ctx: ToolContext) -> ToolReturn:
    """Run one tool call and register the result as a citable record.

    Unknown tool names and tool-level failures become error results rather
    than exceptions — the model sees the error and can course-correct.
    """

    arguments = arguments or {}
    func = _TOOLS.get(name)
    if func is None:
        result: dict[str, Any] = {"error": f"unknown tool {name!r}"}
    else:
        result = func(arguments, ctx)
    content = json.dumps(result, indent=2, default=str)[:_CONTENT_LIMIT]
    ret = ToolReturn(
        id=f"T{len(ctx.citations) + 1}",
        tool=name,
        arguments=arguments,
        content=content,
        ok="error" not in result,
    )
    ctx.citations.append(ret)
    return ret
