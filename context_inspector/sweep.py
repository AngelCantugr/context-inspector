"""Sweep: analyze every local agent session and aggregate the results.

Answers "what does my actual usage look like?" across all sessions from
supported harnesses, instead of cherry-picked examples. Per harness it reports
session count, window sizes, category shares, and how often each bloat signal
fires — plus the largest outlier sessions worth opening individually.
"""

from __future__ import annotations

import glob
import json
import os
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from .adapters import load_any
from .analyzer import analyze

KIMI_HOME = os.path.expanduser(
    os.environ.get(
        "KIMI_CODE_HOME",
        "~/Library/Application Support/kimi-desktop/daimon-share/daimon/runtime/kimi-code/home",
    )
)


def discover_sessions(include_subagents: bool = False) -> list[tuple[str, str]]:
    """Find all session logs for supported harnesses. Returns (source, path).

    Claude Code subagent logs (/subagents/*.jsonl) are zero-token stubs of
    separate contexts — skipped by default, hence the much lower file count
    vs. a raw recursive glob.
    """
    found: list[tuple[str, str]] = []
    for p in glob.glob(os.path.expanduser("~/.claude/projects/**/*.jsonl"), recursive=True):
        if not include_subagents and "/subagents/" in p:
            continue
        found.append(("claude-code", p))
    for p in glob.glob(os.path.expanduser("~/.codex/sessions/**/rollout-*.jsonl"), recursive=True):
        found.append(("codex", p))
    for p in glob.glob(os.path.join(KIMI_HOME, "sessions", "*", "*", "agents", "main", "wire.jsonl")):
        found.append(("kimi-code", p))  # 'main' only — subagent contexts excluded, like Claude sidechains
    return found


SOURCE_NOTES = {
    "claude-code": (
        "append-only log: totals are cumulative content, not the final window "
        "(compaction markers found in many large sessions); system prompt counted "
        "only when a prompt_snapshot exists (~26% of logs)"
    ),
    "codex": (
        "reasoning items skipped (opaque); base_instructions recorded only from "
        "2026-02 onward; <environment_context>/<user_action> surfaced as "
        "'harness context'"
    ),
    "kimi-code": (
        "system prompt and tool schemas are hash-only in the wire log — "
        "injected context is NOT included in totals"
    ),
}


@dataclass
class SessionResult:
    source: str
    path: str
    total_tokens: int
    category_tokens: dict[str, int]
    n_duplicates: int
    wasted_tokens: int
    signals: list[str] = field(default_factory=list)
    tool_tokens: dict[str, int] = field(default_factory=dict)
    tool_counts: dict[str, int] = field(default_factory=dict)


def _wasted(report) -> int:
    return sum(
        report.messages[i].tokens for i in report.duplicates if i < len(report.messages)
    )


def sweep_one(source: str, path: str, tokenizer=None) -> SessionResult:
    messages, _ = load_any(path, source)
    report = analyze(messages, tokenizer=tokenizer)
    return SessionResult(
        source=source,
        path=path,
        total_tokens=report.total_tokens,
        category_tokens=dict(report.category_tokens),
        n_duplicates=len(report.duplicates),
        wasted_tokens=_wasted(report),
        signals=list(report.signals),
        tool_tokens=dict(report.tool_tokens),
        tool_counts=dict(report.tool_counts),
    )


SIGNAL_KEYS = {
    "tool results": "tool results >40% of window",
    "system prompt": "system prompt >25% of window",
    "duplicate": "exact duplicates present",
    "alone is": "single message >30% of window",
}


def _signal_hits(signals: list[str]) -> set[str]:
    hits = set()
    for sig in signals:
        for key, label in SIGNAL_KEYS.items():
            if key in sig:
                hits.add(label)
    return hits


def aggregate(results: list[SessionResult]) -> dict:
    by_source: dict[str, list[SessionResult]] = {}
    for r in results:
        by_source.setdefault(r.source, []).append(r)

    out: dict[str, dict] = {}
    for source, rs in by_source.items():
        totals = [r.total_tokens for r in rs]
        cat_sum: dict[str, int] = {}
        for r in rs:
            for cat, tok in r.category_tokens.items():
                cat_sum[cat] = cat_sum.get(cat, 0) + tok
        grand = sum(cat_sum.values()) or 1
        signal_counts: dict[str, int] = {}
        for r in rs:
            for label in _signal_hits(r.signals):
                signal_counts[label] = signal_counts.get(label, 0) + 1
        with_dupes = [r for r in rs if r.n_duplicates]
        out[source] = {
            "sessions": len(rs),
            "total_tokens": sum(totals),
            "median_window": int(statistics.median(totals)),
            "max_window": max(totals),
            "category_shares": {
                c: round(v / grand, 3) for c, v in sorted(cat_sum.items(), key=lambda kv: -kv[1])
            },
            "signal_frequency": {
                label: round(n / len(rs), 3) for label, n in sorted(signal_counts.items())
            },
            "sessions_with_duplicates": len(with_dupes),
            "total_wasted_tokens": sum(r.wasted_tokens for r in rs),
        }
    return out


def tool_ranking(results: list[SessionResult]) -> dict[str, dict[str, int]]:
    """Tool-result tokens per tool name, per source plus 'all'."""
    by_source: dict[str, dict[str, int]] = {}
    overall: dict[str, int] = {}
    for r in results:
        src = by_source.setdefault(r.source, {})
        for name, tokens in r.tool_tokens.items():
            src[name] = src.get(name, 0) + tokens
            overall[name] = overall.get(name, 0) + tokens
    by_source["all"] = overall
    return by_source


def render_sweep(results: list[SessionResult], agg: dict) -> str:
    lines = []
    w = 72
    lines.append("=" * w)
    lines.append("CONTEXT INSPECTOR — full sweep across local sessions")
    lines.append("=" * w)
    lines.append(f"{len(results)} sessions analyzed\n")

    for source, a in agg.items():
        lines.append(f"── {source} " + "─" * (w - len(source) - 4))
        lines.append(
            f"  sessions: {a['sessions']}   total: ~{a['total_tokens']:,} tokens   "
            f"median window: ~{a['median_window']:,}   largest: ~{a['max_window']:,}"
        )
        lines.append("  category shares (token-weighted across sessions):")
        for cat, share in a["category_shares"].items():
            bar = "#" * max(1, int(share * 30))
            lines.append(f"    {cat:<15} {share:>5.0%} {bar}")
        lines.append("  signal frequency (share of sessions firing):")
        if a["signal_frequency"]:
            for label, freq in a["signal_frequency"].items():
                lines.append(f"    {label:<35} {freq:>5.0%}")
        else:
            lines.append("    (none)")
        lines.append(
            f"  duplicates: {a['sessions_with_duplicates']}/{a['sessions']} sessions, "
            f"~{a['total_wasted_tokens']:,} tokens in repeated content"
        )
        if source in SOURCE_NOTES:
            lines.append(f"  note: {SOURCE_NOTES[source]}")
        lines.append("")

    tools = tool_ranking(results)
    overall = tools.get("all", {})
    if overall:
        grand = sum(overall.values()) or 1
        lines.append("── top tools by result tokens (all sessions) " + "─" * 28)
        for name, tok in sorted(overall.items(), key=lambda kv: -kv[1])[:8]:
            lines.append(f"  {name:<28} {tok:>11,} ({tok / grand:>4.0%})")
        for src in ("claude-code", "codex", "kimi-code"):
            src_tools = tools.get(src) or {}
            if not src_tools:
                continue
            top3 = sorted(src_tools.items(), key=lambda kv: -kv[1])[:3]
            src_total = sum(src_tools.values()) or 1
            summary = "  ·  ".join(f"{n} {t / src_total:.0%}" for n, t in top3)
            lines.append(f"    [{src}] {summary}")
        lines.append("")

    top = sorted(results, key=lambda r: -r.total_tokens)[:5]
    lines.append("── top 5 largest sessions " + "─" * 46)
    for r in top:
        lines.append(f"  ~{r.total_tokens:>8,} tok  [{r.source}] {r.path}")
    return "\n".join(lines)


def to_markdown(results: list[SessionResult], agg: dict) -> str:
    lines = ["# Context sweep — all local agent sessions", ""]
    lines.append(f"{len(results)} sessions analyzed.\n")
    for source, a in agg.items():
        lines.append(f"## {source}\n")
        lines.append(
            f"- Sessions: {a['sessions']} · total ~{a['total_tokens']:,} tokens · "
            f"median window ~{a['median_window']:,} · largest ~{a['max_window']:,}"
        )
        lines.append(f"- Duplicates: {a['sessions_with_duplicates']}/{a['sessions']} sessions, "
                     f"~{a['total_wasted_tokens']:,} tokens in repeated content\n")
        if source in SOURCE_NOTES:
            lines.append(f"- Note: {SOURCE_NOTES[source]}\n")
        lines.append("| Category | Share |")
        lines.append("|---|---|")
        for cat, share in a["category_shares"].items():
            lines.append(f"| {cat} | {share:.0%} |")
        lines.append("")
        if a["signal_frequency"]:
            lines.append("| Signal | Sessions firing |")
            lines.append("|---|---|")
            for label, freq in a["signal_frequency"].items():
                lines.append(f"| {label} | {freq:.0%} |")
            lines.append("")
    tools = tool_ranking(results)
    overall = tools.get("all", {})
    if overall:
        grand = sum(overall.values()) or 1
        lines.append("## Tool attribution (result tokens, all sessions)\n")
        lines.append("| Tool | Tokens | Share |")
        lines.append("|---|---|---|")
        for name, tok in sorted(overall.items(), key=lambda kv: -kv[1])[:12]:
            lines.append(f"| {name} | {tok:,} | {tok / grand:.0%} |")
        lines.append("")
        for src in ("claude-code", "codex", "kimi-code"):
            src_tools = tools.get(src) or {}
            if not src_tools:
                continue
            src_total = sum(src_tools.values()) or 1
            top3 = sorted(src_tools.items(), key=lambda kv: -kv[1])[:3]
            summary = " · ".join(f"**{n}** {t / src_total:.0%}" for n, t in top3)
            lines.append(f"- **{src}**: {summary}")
        lines.append("")
    lines.append("## Top 5 largest sessions\n")
    for r in sorted(results, key=lambda r: -r.total_tokens)[:5]:
        lines.append(f"- ~{r.total_tokens:,} tokens `[{r.source}]` `{r.path}`")
    lines.append("")
    return "\n".join(lines)


def run_sweep(tokenizer=None) -> tuple[list[SessionResult], dict]:
    sessions = discover_sessions()
    results: list[SessionResult] = []
    for source, path in sessions:
        try:
            results.append(sweep_one(source, path, tokenizer=tokenizer))
        except Exception:
            continue  # skip corrupt/partial logs, keep the sweep moving
    return results, aggregate(results)
