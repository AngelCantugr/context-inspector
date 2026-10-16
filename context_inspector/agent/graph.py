"""LangGraph interpretation stage of the weekly pipeline (#15).

Graph: ``load_context → deep_dive (one iteration per outlier) →
gate_findings → interpret → compose_memo → file_note``. The citation
gate runs BEFORE interpretation so rejected (uncited) findings can never
shape — or be filed alongside — the narrative and experiment; compose
re-applies it at assembly time. The agent interprets evidence gathered
by a hard-coded inspect-only tool surface (tools.py); deterministic
counting stays outside the graph.

Failure policy: an Anthropic API call is retried once after a backoff,
then ``AgentStageError`` is raised — the caller skips the week rather
than filing a half-invented memo. Cost is bounded by a cap on total LLM
iterations and a per-outlier budget on ``read_window`` calls. The file
tools only open sessions authorized by the manifest/snapshot allowlist.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, StateGraph

from .memo import Finding, Memo, compose_memo, render_markdown, validate_findings
from .prompts import SYSTEM, deep_dive_messages, interpret_messages
from .tools import TOOL_SCHEMAS, ToolContext, execute_tool

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5-5"


class AgentError(RuntimeError):
    """Base class for interpretation-agent failures."""


class AgentPreflightError(AgentError):
    """Configuration problem detected before any API call."""


class AgentStageError(AgentError):
    """Agent-stage failure after retry — skip the week, file nothing."""


class MessagesAPI(Protocol):
    """The slice of the Anthropic client the graph needs (injectable)."""

    def create(self, **kwargs) -> Any: ...


@dataclass
class AgentConfig:
    """Configuration for one interpretation run.

    ``client`` overrides Anthropic client construction entirely (tests
    inject a fake); otherwise a client is built from ``ANTHROPIC_API_KEY``
    and ``model`` at run time.
    """

    notes_dir: Path | None = None  # vault reports folder for filing
    model: str = DEFAULT_MODEL
    client: MessagesAPI | None = None
    max_iterations: int = 24  # total LLM calls across the whole graph
    max_reads_per_outlier: int = 3  # read_window budget per deep dive
    backoff_seconds: float = 2.0  # delay before the one API retry
    max_tokens: int = 2048
    root: Path = Path(".")


class AgentState(TypedDict, total=False):
    """LangGraph state for the interpretation run (all channels optional)."""

    manifest: dict
    config: AgentConfig
    client: MessagesAPI
    ctx: ToolContext
    context: str
    outliers: list[dict]
    outlier_index: int
    iterations: int
    findings: list[Finding]
    rejected_findings: list[Finding]
    trend_narrative: str
    experiment: str
    memo: Memo
    memo_markdown: str
    filed_path: str


# --------------------------------------------------------------------- nodes

def _load_context(state: AgentState) -> dict:
    """Summarize the manifest and snapshot for the model (no LLM call)."""

    manifest = state["manifest"]
    ctx = ToolContext(
        root=Path(manifest.get("root") or "."),
        snapshot_path=state["config"].root / manifest["snapshot"],
        max_reads_per_outlier=state["config"].max_reads_per_outlier,
    )
    state["ctx"] = ctx
    state["outliers"] = list(manifest.get("outliers") or [])
    state["outlier_index"] = 0
    state["iterations"] = 0
    state["findings"] = []
    state["rejected_findings"] = []

    try:
        from ..weekly import load_results

        records = load_results(ctx.snapshot_path)
        total_tokens = sum(int(r.get("total_tokens") or 0) for r in records.values())
        by_source: dict[str, int] = {}
        for r in records.values():
            src = str(r.get("source", "?"))
            by_source[src] = by_source.get(src, 0) + 1
        snapshot_summary = (
            f"{len(records)} sessions, {total_tokens:,} total tokens; "
            f"by source: {json.dumps(by_source, sort_keys=True)}"
        )
        session_paths = list(records)
    except Exception as exc:
        log.warning("snapshot summary failed: %s", exc)
        snapshot_summary = f"snapshot unreadable: {exc}"
        session_paths = []

    # The file tools may only touch sessions this run is about: the deep-dive
    # outliers plus every session in the snapshot, resolved once here so
    # traversal/symlink tricks land outside the allowlist.
    ctx.allowed_paths = {
        p
        for raw in [o["path"] for o in state["outliers"]] + session_paths
        if raw
        for p in [Path(raw).expanduser().resolve()]
    }

    outlier_lines = "\n".join(
        f"- {o['path']}: {o.get('reason', '')}"
        for o in state["outliers"]
    ) or "(none)"
    state["context"] = (
        f"Generated: {manifest.get('generated')}\n"
        f"Baseline week (no previous snapshot): {manifest.get('baseline')}\n"
        f"Snapshot: {manifest.get('snapshot')} — {snapshot_summary}\n"
        f"Selected outliers ({len(state['outliers'])}):\n{outlier_lines}"
    )
    return state


def _call_model(state: AgentState, messages: list[dict], tools: list[dict] | None) -> Any:
    """One Anthropic call through the one-retry-with-backoff policy."""

    config = state["config"]
    kwargs: dict[str, Any] = {
        "model": config.model,
        "max_tokens": config.max_tokens,
        "system": SYSTEM,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
    client = state["client"]
    try:
        return client.messages.create(**kwargs)
    except Exception as first:
        log.warning("Anthropic call failed (%s); retrying once", first)
        if config.backoff_seconds > 0:
            time.sleep(config.backoff_seconds)
        try:
            return client.messages.create(**kwargs)
        except Exception as second:
            raise AgentStageError(
                f"Anthropic API failed after one retry: {second}"
            ) from second


def _content_blocks(response: Any) -> list[Any]:
    return list(getattr(response, "content", None) or [])


def _block_type(block: Any) -> str:
    return getattr(block, "type", "") or (block.get("type", "") if isinstance(block, dict) else "")


def _block_get(block: Any, key: str) -> Any:
    if isinstance(block, dict):
        return block.get(key)
    return getattr(block, key, None)


def _block_to_dict(block: Any) -> Any:
    """Serialize an SDK content block (or test double) back to a plain dict."""

    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump()
    if hasattr(block, "__dict__"):
        return {k: v for k, v in vars(block).items() if not k.startswith("_")}
    return block


def _extract_json(text: str) -> dict | None:
    """Parse the model's raw-JSON reply; tolerate surrounding whitespace."""

    text = text.strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start, depth = -1, 0
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    parsed = json.loads(text[start : i + 1])
                    return parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def _deep_dive(state: AgentState) -> dict:
    """One graph iteration per outlier: bounded tool-calling loop."""

    config = state["config"]
    ctx: ToolContext = state["ctx"]
    outliers: list[dict] = state["outliers"]
    index = state["outlier_index"]
    outlier = outliers[index]
    ctx.reads_used = 0  # fresh read budget per outlier

    messages = deep_dive_messages(state["context"], outlier, index + 1, len(outliers))
    evidence_start = len(ctx.citations)  # only evidence from THIS deep dive is in scope
    finding: dict | None = None
    while finding is None:
        if state["iterations"] >= config.max_iterations:
            raise AgentStageError(
                f"iteration cap ({config.max_iterations}) hit during deep dive "
                f"of {outlier['path']} — skipping the week"
            )
        state["iterations"] += 1
        response = _call_model(state, messages, TOOL_SCHEMAS)
        blocks = _content_blocks(response)
        tool_uses = [b for b in blocks if _block_type(b) == "tool_use"]
        if tool_uses:
            # Replay the ORIGINAL assistant content verbatim — thinking and
            # redacted_thinking blocks (with signatures) must survive the
            # round-trip or the API rejects the next call with a 400.
            messages.append(
                {"role": "assistant", "content": [_block_to_dict(b) for b in blocks]}
            )
            tool_results = []
            for block in tool_uses:
                name = _block_get(block, "name") or "?"
                arguments = _block_get(block, "input") or {}
                tool_id = _block_get(block, "id") or ""
                ret = execute_tool(name, arguments, ctx)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": f"[{ret.id}] {ret.content}",
                    }
                )
            messages.append({"role": "user", "content": tool_results})
            continue
        text = "\n".join(
            str(_block_get(b, "text") or "") for b in blocks if _block_type(b) == "text"
        )
        finding = _extract_json(text)

    if not finding:
        raise AgentStageError(
            f"deep dive of {outlier['path']} produced no parseable finding"
        )
    citations = finding.get("citations")
    # Scope: this dive's own evidence + week-level run_query evidence from
    # anywhere in the run. Session-specific evidence from other dives is
    # excluded — the gate rejects citations the finding never saw.
    scope = {c.id for c in ctx.citations[evidence_start:]} | {
        c.id for c in ctx.citations if c.tool == "run_query"
    }
    state["findings"] = state["findings"] + [
        Finding(
            session_path=outlier["path"],
            pattern=str(finding.get("pattern") or "Unnamed pattern"),
            likely_cause=str(finding.get("likely_cause") or ""),
            prescription=str(finding.get("prescription") or ""),
            citations=[str(c) for c in citations] if isinstance(citations, list) else [],
            scope=scope,
        )
    ]
    state["outlier_index"] = index + 1
    return state


def _more_outliers(state: AgentState) -> str:
    return (
        "deep_dive"
        if state["outlier_index"] < len(state["outliers"])
        else "gate_findings"
    )


def _gate_findings(state: AgentState) -> dict:
    """Citation gate BEFORE interpretation.

    Rejected findings never reach the interpret stage, so the narrative
    and experiment cannot be derived from (or filed alongside) uncited
    findings. With nothing accepted, interpret is skipped entirely.
    """

    kept, rejected = validate_findings(state["findings"], state["ctx"].citations)
    state["findings"] = kept
    state["rejected_findings"] = rejected
    return state


def _after_gate(state: AgentState) -> str:
    return "interpret" if state["findings"] else "compose_memo"


def _interpret(state: AgentState) -> dict:
    """Trend narrative + next week's experiment from the cited findings."""

    if state["iterations"] >= state["config"].max_iterations:
        raise AgentStageError(
            f"iteration cap ({state['config'].max_iterations}) hit before interpret"
        )
    state["iterations"] += 1
    findings = [
        {
            "session": f.session_path,
            "pattern": f.pattern,
            "citations": f.citations,
        }
        for f in state["findings"]
    ]
    messages = interpret_messages(
        state["context"], findings, bool(state["manifest"].get("baseline"))
    )
    response = _call_model(state, messages, None)
    text = "\n".join(
        str(_block_get(b, "text") or "")
        for b in _content_blocks(response)
        if _block_type(b) == "text"
    )
    parsed = _extract_json(text)
    if parsed is None:
        raise AgentStageError("interpret stage produced no parseable JSON")
    state["trend_narrative"] = str(parsed.get("trend_narrative") or "")
    state["experiment"] = str(parsed.get("experiment") or "")
    return state


def _week_id(generated: str) -> str:
    year, week, _ = date.fromisoformat(generated).isocalendar()
    return f"{year}-W{week:02d}"


def _compose_memo(state: AgentState) -> dict:
    """Re-apply the citation gate (idempotent) + deterministic assembly."""

    manifest = state["manifest"]
    memo = compose_memo(
        week_id=_week_id(manifest["generated"]),
        date=manifest["generated"],
        baseline=bool(manifest.get("baseline")),
        findings=state["findings"],
        citations=state["ctx"].citations,
        trend_narrative=state.get("trend_narrative", ""),
        experiment=state.get("experiment", ""),
        rejected_findings=state["rejected_findings"],
    )
    state["memo"] = memo
    state["memo_markdown"] = render_markdown(memo)
    return state


def _file_note(state: AgentState) -> dict:
    """Write the memo to the vault reports folder."""

    config = state["config"]
    if config.notes_dir is None:
        raise AgentPreflightError("notes_dir (vault reports folder) is not configured")
    notes_dir = Path(config.notes_dir)
    notes_dir.mkdir(parents=True, exist_ok=True)
    path = notes_dir / f"context-hygiene-weekly-{state['memo'].week_id}.md"
    path.write_text(state["memo_markdown"], encoding="utf-8")
    memo: Memo = state["memo"]
    memo.filed_path = str(path)
    state["filed_path"] = str(path)
    return state


def build_graph() -> Any:
    """Compile the interpretation graph."""

    graph = StateGraph(AgentState)
    graph.add_node("load_context", _load_context)
    graph.add_node("deep_dive", _deep_dive)
    graph.add_node("interpret", _interpret)
    graph.add_node("gate_findings", _gate_findings)
    graph.add_node("compose_memo", _compose_memo)
    graph.add_node("file_note", _file_note)
    graph.set_entry_point("load_context")
    graph.add_conditional_edges(
        "load_context", lambda s: "deep_dive" if s["outliers"] else "gate_findings"
    )
    graph.add_conditional_edges("deep_dive", _more_outliers)
    graph.add_conditional_edges("gate_findings", _after_gate)
    graph.add_edge("interpret", "compose_memo")
    graph.add_edge("compose_memo", "file_note")
    graph.add_edge("file_note", END)
    return graph.compile()


def _build_client(config: AgentConfig) -> MessagesAPI:
    """Anthropic client from the environment (preflight-checked)."""

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise AgentPreflightError(
            "ANTHROPIC_API_KEY is not set — refusing to call the API"
        )
    try:
        import anthropic
    except ImportError:
        raise AgentPreflightError(
            "anthropic SDK is not installed — install the agent extra with "
            '`uv sync --extra agent` (or `pip install "context-inspector[agent]"`)'
        ) from None
    return anthropic.Anthropic(api_key=key)


def run_interpretation(
    manifest: dict, config: AgentConfig | None = None
) -> Memo:
    """Run the interpretation agent over a weekly deepdive manifest.

    Returns the filed Memo. ``AgentStageError`` means the week must be
    skipped (no memo is filed); ``AgentPreflightError`` means the run was
    never safe to start.
    """

    config = config or AgentConfig()
    if config.notes_dir is None:
        raise AgentPreflightError("notes_dir (vault reports folder) is not configured")
    client = config.client if config.client is not None else _build_client(config)
    state = AgentState(manifest=manifest, config=config, client=client)
    final = build_graph().invoke(state)
    return final["memo"]
