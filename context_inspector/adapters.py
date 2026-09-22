"""Adapters: convert agent-harness session logs into OpenAI-format message lists.

Each adapter takes a session-log path and returns the message list the way the
analyzer expects it: ``{"role": ..., "content": ...}`` plus optional
``tool_calls`` on assistant turns. Token accounting rules stay identical across
harnesses, so breakdowns are comparable.

Known limitations (by harness):

- **Claude Code**: the log is an append-only event stream. Subagent runs
  (``isSidechain: true``) are separate contexts and are skipped by default.
  Thinking blocks count toward the assistant turn — they do occupy the window.
  The system prompt is only visible when a ``prompt_snapshot`` attachment
  exists (~26% of logs); sessions without one understate injected context.
- **Codex CLI**: ``reasoning`` items are skipped (content is opaque/encrypted
  summaries, not window-visible text). The base instructions from
  ``session_meta`` are counted as the system prompt (recorded only from
  2026-02 onward). Harness-injected ``<environment_context>`` /
  ``<user_action>`` wrappers — logged as user-role messages — are surfaced
  as a ``harness context`` category so they don't inflate "user".
- **Kimi Code**: the wire log stores only a hash of the system prompt and tool
  schemas (``llm.request`` / ``tools.register_user_tool``), so their weight is
  *not* included in the total. User/assistant/tool traffic is exact.
"""

from __future__ import annotations

import json
from pathlib import Path

SOURCES = ("claude-code", "codex", "kimi-code")


def _read_jsonl(path: str | Path) -> list[dict]:
    records = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue  # tolerate truncated last lines in live logs
        if isinstance(obj, dict):
            records.append(obj)
    return records


def _text_of(blocks: list | str | None) -> str:
    """Flatten Anthropic-style content blocks to plain text."""
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, list):
        return ""
    parts = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("type") in ("text", "input_text", "output_text"):
            parts.append(b.get("text", ""))
        elif b.get("type") == "thinking":
            parts.append(b.get("thinking", ""))
    return "\n".join(p for p in parts if p)


def _tool_call(name: str, arguments, call_id: str = "") -> dict:
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments, ensure_ascii=False)
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name or "?", "arguments": arguments},
    }


# ---------------------------------------------------------------- Claude Code

def from_claude_code(path: str | Path) -> list[dict]:
    """Convert a Claude Code session log (~/.claude/projects/**/<id>.jsonl)."""
    messages: list[dict] = []
    last_snapshot = None  # identical snapshots repeat in logs — count each era once
    for rec in _read_jsonl(path):
        if rec.get("isSidechain"):
            continue  # subagent context, not the main window

        # prompt_snapshot attachments carry the actual system prompt —
        # without them the injected context is invisible (present in ~26% of logs)
        attachment = rec.get("attachment") or {}
        if attachment.get("type") == "prompt_snapshot":
            parts = attachment.get("systemPrompt") or []
            text = "\n".join(p for p in parts if isinstance(p, str) and p)
            if text and text != last_snapshot:
                last_snapshot = text
                messages.append({"role": "system", "content": text})

        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")

        if role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            if isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text":
                        text_parts.append(b.get("text", ""))
                    elif b.get("type") == "thinking":
                        text_parts.append(b.get("thinking", ""))
                    elif b.get("type") == "tool_use":
                        tool_calls.append(
                            _tool_call(b.get("name", ""), b.get("input", {}), b.get("id", ""))
                        )
            out = {"role": "assistant", "content": "\n".join(t for t in text_parts if t)}
            if tool_calls:
                out["tool_calls"] = tool_calls
            messages.append(out)

        elif role == "user":
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        messages.append(
                            {
                                "role": "tool",
                                "content": _text_of(b.get("content")),
                                "tool_call_id": b.get("tool_use_id", ""),
                                "name": b.get("name", "") or "tool",
                            }
                        )
                    elif isinstance(b, dict) and b.get("type") == "text":
                        text = b.get("text", "")
                        if text:
                            messages.append({"role": "user", "content": text})
            elif isinstance(content, str) and content:
                messages.append({"role": "user", "content": content})

        elif role in ("system", "tool"):
            messages.append({"role": role, "content": _text_of(content)})
    return messages


# ------------------------------------------------------------------ Codex CLI

def from_codex(path: str | Path) -> list[dict]:
    """Convert a Codex CLI rollout (~/.codex/sessions/**/rollout-*.jsonl)."""
    messages: list[dict] = []
    for rec in _read_jsonl(path):
        payload = rec.get("payload", {})

        # session_meta carries the base instructions = the system prompt
        if rec.get("type") == "session_meta":
            base = payload.get("base_instructions") or {}
            text = base.get("text") if isinstance(base, dict) else None
            if text:
                messages.append({"role": "system", "content": text})
            continue

        if rec.get("type") != "response_item":
            continue
        ptype = payload.get("type")

        if ptype == "message":
            role = payload.get("role", "user")
            text = _text_of(payload.get("content"))
            if text:
                # harness-injected wrappers arrive as user-role messages;
                # surface them as their own category instead of inflating "user"
                if role == "user" and text.lstrip().startswith(
                    ("<environment_context>", "<user_action>")
                ):
                    role = "harness context"
                messages.append({"role": role, "content": text})

        elif ptype in ("function_call", "custom_tool_call"):
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call(
                            payload.get("name", ""),
                            payload.get("arguments", payload.get("input", "")),
                            payload.get("call_id", ""),
                        )
                    ],
                }
            )

        elif ptype in ("function_call_output", "custom_tool_call_output"):
            output = payload.get("output", "")
            if isinstance(output, list):  # newer rollouts: list of text parts
                output = _text_of(output)
            elif not isinstance(output, str):
                output = json.dumps(output, ensure_ascii=False)
            messages.append(
                {
                    "role": "tool",
                    "content": output,
                    "tool_call_id": payload.get("call_id", ""),
                    "name": "tool",
                }
            )
        # "reasoning" items intentionally skipped — see module docstring
    return messages


# ------------------------------------------------------------- Kimi Code/Work

def from_kimi_code(path: str | Path) -> list[dict]:
    """Convert a Kimi Code/Work session wire log (.../agents/main/wire.jsonl)."""
    messages: list[dict] = []
    for rec in _read_jsonl(path):
        rtype = rec.get("type")

        if rtype == "context.append_message":
            msg = rec.get("message", {})
            role = msg.get("role", "user")
            text = _text_of(msg.get("content"))
            if text:
                messages.append({"role": role, "content": text})

        elif rtype == "context.append_loop_event":
            event = rec.get("event", {})
            etype = event.get("type")

            if etype == "content.part":
                part = event.get("part", {})
                if part.get("type") == "text" and part.get("text"):
                    messages.append({"role": "assistant", "content": part["text"]})
                elif part.get("type") == "think" and part.get("think"):
                    messages.append({"role": "assistant", "content": part["think"]})

            elif etype == "tool.call":
                messages.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            _tool_call(
                                event.get("name", ""),
                                event.get("args", {}),
                                event.get("toolCallId", ""),
                            )
                        ],
                    }
                )

            elif etype == "tool.result":
                result = event.get("result", {})
                messages.append(
                    {
                        "role": "tool",
                        "content": str(result.get("output", "")),
                        "tool_call_id": event.get("toolCallId", ""),
                        "name": "tool",
                    }
                )
        # system prompt + tool schemas are hash-only in the wire log (see docstring)
    return messages


# ------------------------------------------------------------------- plumbing

_ADAPTERS = {
    "claude-code": from_claude_code,
    "codex": from_codex,
    "kimi-code": from_kimi_code,
}


def _sniff_source(path: str | Path) -> str | None:
    """Identify the harness from the first parseable line's shape.

    Returns a source name, or None for plain OpenAI JSON (or anything
    unrecognizable — path heuristics in detect_source handle the rest).
    """
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    return None  # e.g. pretty-printed OpenAI JSON array file
                if not isinstance(obj, dict):
                    return None
                t = obj.get("type")
                if t == "session_meta" or (t == "response_item" and "payload" in obj):
                    return "codex"
                if t in ("context.append_message", "context.append_loop_event"):
                    return "kimi-code"
                if t in ("user", "assistant", "system", "attachment") or (
                    "message" in obj and ("parentUuid" in obj or "uuid" in obj)
                ):
                    return "claude-code"
                if "role" in obj or "messages" in obj:
                    return None  # plain OpenAI messages (JSONL or array file)
                return None
    except OSError:
        pass
    return None


def _detect_by_path(path: str | Path) -> str | None:
    """Fallback when content sniffing is inconclusive (empty/unreadable file)."""
    p = str(path)
    if ".claude" in p and p.endswith(".jsonl"):
        return "claude-code"
    if ".codex" in p or "rollout-" in Path(p).name:
        return "codex"
    if Path(p).name == "wire.jsonl":
        return "kimi-code"
    return None


def detect_source(path: str | Path) -> str | None:
    """Best-effort harness detection; None means 'plain OpenAI JSON'."""
    return _sniff_source(path) or _detect_by_path(path)


def load_any(path: str | Path, source: str | None = None) -> tuple[list[dict], str | None]:
    """Load a transcript, converting via the right adapter when needed.

    Returns (messages, detected_source). Plain OpenAI-format JSON passes
    through with detected_source None.
    """
    source = source or detect_source(path)
    if source is None:
        from .analyzer import load_transcript

        return load_transcript(path), None
    if source not in _ADAPTERS:
        raise ValueError(f"unknown source '{source}' (expected one of {SOURCES})")
    try:
        return _ADAPTERS[source](path), source
    except Exception:
        # misdetected? fall back to plain OpenAI parsing before giving up
        from .analyzer import load_transcript

        return load_transcript(path), None
