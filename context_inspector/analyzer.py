"""Analyze an OpenAI-format chat transcript for context bloat."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .estimator import estimate_tokens

CATEGORIES = ("system", "user", "assistant", "tool result", "tool call args")


@dataclass
class MessageStat:
    index: int
    role: str
    name: str
    category: str
    tokens: int
    cumulative: int


@dataclass
class Report:
    messages: list[MessageStat]
    category_tokens: dict[str, int] = field(default_factory=dict)
    total_tokens: int = 0
    duplicates: list[int] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    tool_tokens: dict[str, int] = field(default_factory=dict)
    tool_counts: dict[str, int] = field(default_factory=dict)


def load_transcript(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):  # allow {"messages": [...]}
        data = data["messages"]
    if not isinstance(data, list):
        raise ValueError("transcript must be a JSON list of messages")
    return data


def _message_text(msg: dict, tokenizer) -> tuple[str, int]:
    """Return (category, tokens) for one message.

    Tool-call arguments count toward the assistant turn; tool responses
    get their own category — that's where bloat usually lives.
    """
    role = msg.get("role", "unknown")
    content = msg.get("content") or ""
    tokens = tokenizer(content)

    if role == "assistant":
        args_tokens = sum(
            tokenizer(json.dumps(tc.get("function", {}).get("arguments", "")))
            for tc in (msg.get("tool_calls") or [])
        )
        return "assistant", tokens + args_tokens

    if role == "tool":
        return "tool result", tokens

    return role, tokens


def analyze(messages: list[dict], tokenizer=None) -> Report:
    tokenizer = tokenizer or estimate_tokens
    stats: list[MessageStat] = []
    cumulative = 0
    category_tokens = {c: 0 for c in CATEGORIES}
    id2name: dict[str, str] = {}
    tool_tokens: dict[str, int] = {}
    tool_counts: dict[str, int] = {}

    for i, msg in enumerate(messages):
        for tc in msg.get("tool_calls") or []:
            cid = tc.get("id") or ""
            if cid:
                id2name[cid] = tc.get("function", {}).get("name") or "?"
        category, tokens = _message_text(msg, tokenizer)
        cumulative += tokens
        category_tokens.setdefault(category, 0)
        category_tokens[category] += tokens
        if category == "tool result":
            name = id2name.get(msg.get("tool_call_id") or "")
            if not name:
                name = msg.get("name") or "?"
                if name == "tool":
                    name = "?"
            tool_tokens[name] = tool_tokens.get(name, 0) + tokens
            tool_counts[name] = tool_counts.get(name, 0) + 1
        stats.append(
            MessageStat(
                index=i,
                role=msg.get("role", "?"),
                name=msg.get("name", ""),
                category=category,
                tokens=tokens,
                cumulative=cumulative,
            )
        )

    report = Report(
        messages=stats,
        category_tokens={k: v for k, v in category_tokens.items() if v},
        total_tokens=cumulative,
        tool_tokens=tool_tokens,
        tool_counts=tool_counts,
    )
    report.duplicates = _find_duplicates(messages)
    report.signals = _signals(report, stats)
    return report


def _content_key(msg: dict) -> str:
    # tool_calls included: assistant turns that differ only in tool invocations
    # are NOT duplicates, and tool-call-only turns all share empty content
    tool_sig = [
        (tc.get("function", {}).get("name"), tc.get("function", {}).get("arguments"))
        for tc in (msg.get("tool_calls") or [])
    ]
    return json.dumps(
        {
            "role": msg.get("role"),
            "content": msg.get("content"),
            "tool_calls": tool_sig,
        },
        sort_keys=True,
    )


def _find_duplicates(messages: list[dict]) -> list[int]:
    """Indices (after the first) of messages with exact-duplicate content."""
    seen: dict[str, int] = {}
    dupes: list[int] = []
    for i, msg in enumerate(messages):
        content = msg.get("content")
        if (not content or content == "null") and not msg.get("tool_calls"):
            continue  # empty padding messages aren't duplicates, just noise
        key = _content_key(msg)
        if key in seen:
            dupes.append(i)
        else:
            seen[key] = i
    return dupes


def _signals(report: Report, stats: list[MessageStat]) -> list[str]:
    signals: list[str] = []
    total = report.total_tokens or 1

    tool_share = report.category_tokens.get("tool result", 0) / total
    if tool_share > 0.4:
        signals.append(
            f"tool results are {tool_share:.0%} of the window — "
            "the chat is NOT what's eating your context"
        )

    system_share = report.category_tokens.get("system", 0) / total
    if system_share > 0.25:
        signals.append(f"system prompt is {system_share:.0%} of the window")

    if report.duplicates:
        repeated = sum(
            report.messages[i].tokens for i in report.duplicates if i < len(report.messages)
        )
        signals.append(
            f"{len(report.duplicates)} exact duplicate message(s), "
            f"~{repeated} tokens in repeated content"
        )

    if stats:
        biggest = max(stats, key=lambda s: s.tokens)
        if biggest.tokens / total > 0.3:
            signals.append(
                f"message #{biggest.index} ({biggest.category}) alone is "
                f"{biggest.tokens / total:.0%} of the window — truncate or summarize it"
            )

    if report.tool_tokens:
        tool_total = sum(report.tool_tokens.values()) or 1
        top_tool, top_tokens = max(report.tool_tokens.items(), key=lambda kv: kv[1])
        if top_tokens / tool_total > 0.5 and tool_total / total > 0.1:
            signals.append(
                f"tool '{top_tool}' produced {top_tokens / tool_total:.0%} of all "
                f"tool-result tokens — bound its output"
            )

    if not signals:
        signals.append("no major bloat signals detected")
    return signals


def to_dict(report: Report) -> dict:
    return {
        "total_tokens": report.total_tokens,
        "category_tokens": report.category_tokens,
        "tool_tokens": report.tool_tokens,
        "tool_counts": report.tool_counts,
        "signals": report.signals,
        "duplicates": report.duplicates,
        "messages": [
            {
                "index": s.index,
                "role": s.role,
                "name": s.name,
                "category": s.category,
                "tokens": s.tokens,
                "cumulative": s.cumulative,
            }
            for s in report.messages
        ],
    }


def render_text(report: Report) -> str:
    lines = []
    w = 64
    lines.append("=" * w)
    lines.append("CONTEXT INSPECTOR — transcript breakdown")
    lines.append("=" * w)
    header = f"{'#':>3} {'who':<19} {'category':<15} {'tokens':>7} {'cumul.':>8}"
    lines.append(header)
    lines.append("-" * w)
    for s in report.messages:
        label = f"{s.role}{':' + s.name if s.name else ''}"
        lines.append(
            f"{s.index:>3} {label:<19} {s.category:<15} {s.tokens:>7} {s.cumulative:>8}"
        )
    lines.append("-" * w)
    lines.append(f"TOTAL WINDOW: ~{report.total_tokens:,} tokens")
    lines.append("")
    lines.append("By category:")
    for cat, tok in sorted(
        report.category_tokens.items(), key=lambda kv: kv[1], reverse=True
    ):
        share = tok / (report.total_tokens or 1)
        bar = "#" * max(1, int(share * 30))
        lines.append(f"  {cat:<15} {tok:>7} ({share:>4.0%}) {bar}")
    lines.append("")
    if report.tool_tokens:
        tool_total = sum(report.tool_tokens.values()) or 1
        lines.append("Top tools (by result tokens):")
        for name, tok in sorted(
            report.tool_tokens.items(), key=lambda kv: kv[1], reverse=True
        )[:5]:
            share = tok / tool_total
            calls = report.tool_counts.get(name, 0)
            lines.append(
                f"  {name:<19} {tok:>7,} ({share:>4.0%}) "
                f"{calls:>4} call(s), ~{tok // max(calls, 1):,}/call"
            )
        lines.append("")
    lines.append("Bloat signals:")
    for sig in report.signals:
        lines.append(f"  ⚠ {sig}")
    return "\n".join(lines)
