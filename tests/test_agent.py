"""Interpretation-agent tests (#15): mocked-model, injected client.

The fake Anthropic client replays a canned script of responses (or
raised exceptions) and records every ``messages.create`` call, so the
graph runs end-to-end with no network and no API key.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOT = FIXTURES / "sweep_weekB.jsonl"
GENERATED = "2026-10-15"  # ISO week 2026-W42

FINDING = {
    "pattern": "tool results dominate the window",
    "likely_cause": "large Read outputs re-injected every turn",
    "prescription": "truncate Read outputs older than the last two calls",
    "citations": ["T1"],
}

INTERPRET = {
    "trend_narrative": "Tool-result bloat grew across all three outliers.",
    "experiment": "Cap Read output at 200 lines for one week and re-measure.",
}


def _manifest(outliers: list[dict], baseline: bool = False) -> dict:
    return {
        "generated": GENERATED,
        "snapshot": str(SNAPSHOT),
        "previous_snapshot": None if baseline else "reports/snapshots/sweep-2026-10-08.jsonl",
        "baseline": baseline,
        "sessions": 3,
        "removed_sessions": [],
        "outliers": outliers,
    }


def _outlier(path: str = "/tmp/session-a.json") -> dict:
    return {
        "path": path,
        "source": "claude-code",
        "total_tokens": 120_000,
        "growth_tokens": 40_000,
        "wasted_ratio": 0.35,
        "signal_count": 2,
        "score": 2.5,
        "reason": "window 120,000 tokens — largest this week",
    }


def _finding_text(finding: dict | None = None) -> str:
    return json.dumps(finding if finding is not None else FINDING)


def _text_response(payload: dict | str) -> SimpleNamespace:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=text)],
    )


def _tool_use_response(name: str, arguments: dict, tool_id: str = "toolu_1") -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(type="tool_use", id=tool_id, name=name, input=arguments)
        ],
    )


def _thinking_tool_response(
    name: str, arguments: dict, tool_id: str = "toolu_1"
) -> SimpleNamespace:
    """Sonnet-5.5-style response: thinking blocks precede the tool_use."""

    return SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(type="thinking", thinking="let me check the evidence", signature="sig123"),
            SimpleNamespace(type="redacted_thinking", data="...."),
            SimpleNamespace(type="tool_use", id=tool_id, name=name, input=arguments),
        ],
    )


class FakeMessages:
    def __init__(self, script: list):
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("fake client ran out of canned responses")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, script: list):
        self.messages = FakeMessages(script)


def _write_transcript(tmp: str, name: str = "session-a.json") -> str:
    path = Path(tmp) / name
    path.write_text(
        json.dumps(
            [
                {"role": "system", "content": "You are a coding assistant."},
                {"role": "user", "content": "fix the bug"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "Read", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "name": "Read", "content": "x" * 500},
            ]
        ),
        encoding="utf-8",
    )
    return str(path)


class CitationPresenceTest(unittest.TestCase):
    def test_finding_carries_citation_bound_to_tool_return(self):
        from context_inspector.agent import run_interpretation, AgentConfig

        with tempfile.TemporaryDirectory() as tmp:
            transcript = _write_transcript(tmp)
            client = FakeClient(
                [
                    _tool_use_response("analyze_session", {"path": transcript}),
                    _text_response(FINDING),
                    _text_response(INTERPRET),
                ]
            )
            memo = run_interpretation(
                _manifest([_outlier(transcript)]),
                AgentConfig(
                    notes_dir=Path(tmp) / "notes",
                    client=client,
                    backoff_seconds=0,
                ),
            )
            self.assertEqual(len(memo.findings), 1)
            self.assertEqual(memo.findings[0].citations, ["T1"])
            self.assertEqual(memo.citations[0].tool, "analyze_session")
            self.assertEqual(memo.rejected_findings, [])
            self.assertEqual(memo.week_id, "2026-W42")
            note = Path(memo.filed_path)
            self.assertTrue(note.is_file())
            text = note.read_text(encoding="utf-8")
            self.assertIn("type: report", text)
            self.assertIn("prev: context-hygiene-weekly-2026-W41", text)
            self.assertIn("Evidence:** T1", text)
            self.assertIn("## Trend narrative", text)
            # evidence appendix resolves the memo's own citation ids
            self.assertIn("## Evidence", text)
            self.assertIn("### T1 — `analyze_session`", text)
            self.assertIn(transcript, text)  # tool arguments serialized
            self.assertIn('"total_tokens"', text)  # captured result serialized
            # the tool evidence reached the model under a citable id
            tool_result = client.messages.calls[1]["messages"][-1]["content"][0]["content"]
            self.assertTrue(tool_result.startswith("[T1]"))

    def test_baseline_memo_omits_trend_sections(self):
        from context_inspector.agent import run_interpretation, AgentConfig

        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient([_text_response(FINDING), _text_response(INTERPRET)])
            memo = run_interpretation(
                _manifest([_outlier()], baseline=True),
                AgentConfig(notes_dir=Path(tmp), client=client, backoff_seconds=0),
            )
            self.assertTrue(memo.baseline)
            text = Path(memo.filed_path).read_text(encoding="utf-8")
            self.assertNotIn("prev:", text)
            self.assertNotIn("## Trend narrative", text)
            self.assertIn("baseline week", text)


class UncitedRejectionTest(unittest.TestCase):
    def test_finding_without_valid_citation_is_rejected(self):
        from context_inspector.agent import run_interpretation, AgentConfig

        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient(
                [
                    _text_response({**FINDING, "citations": ["T99"]}),
                    _text_response(INTERPRET),
                ]
            )
            memo = run_interpretation(
                _manifest([_outlier()]),
                AgentConfig(notes_dir=Path(tmp), client=client, backoff_seconds=0),
            )
            self.assertEqual(memo.findings, [])
            self.assertEqual(len(memo.rejected_findings), 1)
            self.assertIn("no citation", memo.rejected_findings[0].rejected_reason)
            text = Path(memo.filed_path).read_text(encoding="utf-8")
            self.assertIn("Rejected findings (uncited)", text)

    def test_finding_with_empty_citations_is_rejected(self):
        from context_inspector.agent import run_interpretation, AgentConfig

        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient(
                [
                    _text_response({**FINDING, "citations": []}),
                    _text_response(INTERPRET),
                ]
            )
            memo = run_interpretation(
                _manifest([_outlier()]),
                AgentConfig(notes_dir=Path(tmp), client=client, backoff_seconds=0),
            )
            self.assertEqual(memo.findings, [])
            self.assertEqual(len(memo.rejected_findings), 1)


class IterationCapTest(unittest.TestCase):
    def test_always_tool_using_model_hits_cap_and_hard_fails(self):
        from context_inspector.agent import (
            AgentConfig,
            AgentStageError,
            run_interpretation,
        )

        with tempfile.TemporaryDirectory() as tmp:
            script = [_tool_use_response("read_window", {"path": "/nope", "start_line": 0, "n": 5})] * 10
            client = FakeClient(script)
            with self.assertRaises(AgentStageError):
                run_interpretation(
                    _manifest([_outlier()]),
                    AgentConfig(
                        notes_dir=Path(tmp),
                        client=client,
                        max_iterations=3,
                        backoff_seconds=0,
                    ),
                )
            # bounded: exactly the cap, no runaway loop
            self.assertEqual(len(client.messages.calls), 3)
            # and nothing was filed
            self.assertEqual(list(Path(tmp).rglob("*.md")), [])


class RetryThenFailTest(unittest.TestCase):
    def _api_error(self):
        import anthropic
        import httpx

        return anthropic.APIError(
            "boom",
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
            body=None,
        )

    def test_api_failure_retries_once_then_hard_fails(self):
        from context_inspector.agent import (
            AgentConfig,
            AgentStageError,
            run_interpretation,
        )

        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient([self._api_error(), self._api_error()])
            with self.assertRaises(AgentStageError):
                run_interpretation(
                    _manifest([_outlier()]),
                    AgentConfig(
                        notes_dir=Path(tmp), client=client, backoff_seconds=0
                    ),
                )
            self.assertEqual(len(client.messages.calls), 2)
            self.assertEqual(list(Path(tmp).rglob("*.md")), [])

    def test_single_api_failure_recovers_via_retry(self):
        from context_inspector.agent import run_interpretation, AgentConfig

        with tempfile.TemporaryDirectory() as tmp:
            transcript = _write_transcript(tmp)
            client = FakeClient(
                [
                    self._api_error(),
                    _tool_use_response("analyze_session", {"path": transcript}),
                    _text_response(FINDING),
                    _text_response(INTERPRET),
                ]
            )
            memo = run_interpretation(
                _manifest([_outlier(transcript)]),
                AgentConfig(notes_dir=Path(tmp), client=client, backoff_seconds=0),
            )
            # first deep-dive attempt burns 2 calls on fail+retry, then
            # tool round-trip + finding, then interpret
            self.assertEqual(len(client.messages.calls), 4)
            self.assertEqual(len(memo.findings), 1)


class PreflightTest(unittest.TestCase):
    def test_missing_api_key_fails_before_any_call(self):
        from unittest import mock

        from context_inspector.agent import (
            AgentConfig,
            AgentPreflightError,
            run_interpretation,
        )

        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(AgentPreflightError):
                run_interpretation(
                    _manifest([_outlier()]), AgentConfig(notes_dir=Path("/tmp/none"))
                )

    def test_missing_notes_dir_fails_before_any_call(self):
        from context_inspector.agent import (
            AgentConfig,
            AgentPreflightError,
            run_interpretation,
        )

        with self.assertRaises(AgentPreflightError):
            run_interpretation(
                _manifest([_outlier()]),
                AgentConfig(notes_dir=None, backoff_seconds=0),
            )


class ToolSurfaceTest(unittest.TestCase):
    def test_read_window_clamps_lines_and_enforces_read_budget(self):
        from context_inspector.agent.tools import MAX_WINDOW_LINES, ToolContext, execute_tool

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.jsonl"
            path.write_text("\n".join(f'{{"i": {i}}}' for i in range(200)), "utf-8")
            ctx = ToolContext(
                root=Path(tmp),
                snapshot_path=path,
                max_reads_per_outlier=1,
                allowed_paths={path.resolve()},
            )
            first = execute_tool(
                "read_window", {"path": str(path), "start_line": 10, "n": 500}, ctx
            )
            data = json.loads(first.content)
            self.assertNotIn("error", data)
            self.assertEqual(len(data["lines"]), MAX_WINDOW_LINES)
            self.assertEqual(data["start_line"], 10)
            self.assertEqual(ctx.reads_used, 1)
            second = execute_tool(
                "read_window", {"path": str(path), "start_line": 0, "n": 5}, ctx
            )
            self.assertIn("error", json.loads(second.content))
            self.assertEqual(ctx.reads_used, 1)  # budget-exhausted call does not consume

    def test_analyze_session_missing_path_returns_error_result(self):
        from context_inspector.agent.tools import ToolContext, execute_tool

        with tempfile.TemporaryDirectory() as tmp:
            # deny-by-default: no allowed_paths -> every path is unauthorized
            ctx = ToolContext(root=Path(tmp), snapshot_path=Path(tmp) / "s.jsonl")
            ret = execute_tool("analyze_session", {"path": str(Path(tmp) / "nope.json")}, ctx)
            self.assertIn("error", json.loads(ret.content))
            self.assertEqual(ret.id, "T1")
            self.assertFalse(ret.ok)  # error results are not citable evidence
            self.assertEqual(ctx.citations[0].id, "T1")

    def test_unknown_tool_name_returns_error_result(self):
        from context_inspector.agent.tools import ToolContext, execute_tool

        with tempfile.TemporaryDirectory() as tmp:
            ctx = ToolContext(root=Path(tmp), snapshot_path=Path(tmp) / "s.jsonl")
            ret = execute_tool("delete_everything", {}, ctx)
            data = json.loads(ret.content)
            self.assertIn("unknown tool", data["error"])
            self.assertFalse(ret.ok)


class PathEscapeTest(unittest.TestCase):
    """Finding 1: the file tools only open authorized session files."""

    def test_unauthorized_traversal_and_symlink_paths_are_rejected(self):
        from context_inspector.agent.tools import ToolContext, execute_tool

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            session = base / "session.jsonl"
            session.write_text('{"a": 1}\n' * 5, "utf-8")
            secret = base / "secret.txt"
            secret.write_text("do not read", "utf-8")
            link = base / "link.jsonl"
            link.symlink_to(secret)
            ctx = ToolContext(
                root=base,
                snapshot_path=base / "s.jsonl",
                allowed_paths={session.resolve()},
            )

            ok = execute_tool("read_window", {"path": str(session)}, ctx)
            self.assertNotIn("error", json.loads(ok.content))

            for label, path in (
                ("sibling file", secret),
                ("traversal", base / "subdir" / ".." / "secret.txt"),
                ("symlink escape", link),
            ):
                result = execute_tool("read_window", {"path": str(path)}, ctx)
                self.assertIn(
                    "not an authorized session file",
                    json.loads(result.content)["error"],
                    label,
                )
            analyzed = execute_tool("analyze_session", {"path": str(secret)}, ctx)
            self.assertIn(
                "not an authorized session file",
                json.loads(analyzed.content)["error"],
            )


class RejectedNotInterpretedTest(unittest.TestCase):
    """Finding 2: rejected findings never shape or reach the filed memo's narrative."""

    def test_all_rejected_findings_skip_interpret_and_file_no_narrative(self):
        from context_inspector.agent import run_interpretation, AgentConfig

        with tempfile.TemporaryDirectory() as tmp:
            # Only one canned response: if the graph reached interpret, the
            # fake client would run dry and raise.
            client = FakeClient([_text_response({**FINDING, "citations": ["T99"]})])
            memo = run_interpretation(
                _manifest([_outlier()]),
                AgentConfig(notes_dir=Path(tmp), client=client, backoff_seconds=0),
            )
            self.assertEqual(len(client.messages.calls), 1)  # deep dive only
            self.assertEqual(memo.findings, [])
            self.assertEqual(len(memo.rejected_findings), 1)
            self.assertEqual(memo.trend_narrative, "")
            self.assertEqual(memo.experiment, "")
            text = Path(memo.filed_path).read_text(encoding="utf-8")
            self.assertIn("Rejected findings (uncited)", text)
            self.assertIn("_No citable findings this week._", text)


class ErrorCitationTest(unittest.TestCase):
    """Finding 3 (round 1): citations bound to error results do not count as evidence."""

    def test_citation_to_error_result_is_rejected(self):
        from context_inspector.agent import run_interpretation, AgentConfig

        with tempfile.TemporaryDirectory() as tmp:
            # Authorized outlier path that does not exist: the tool return is
            # a real registry entry (T1) but an error result.
            ghost = str(Path(tmp) / "ghost.json")
            client = FakeClient(
                [
                    _tool_use_response("analyze_session", {"path": ghost}),
                    _text_response(FINDING),  # cites T1 — an error result
                ]
            )
            memo = run_interpretation(
                _manifest([_outlier(ghost)]),
                AgentConfig(notes_dir=Path(tmp), client=client, backoff_seconds=0),
            )
            self.assertEqual(len(client.messages.calls), 2)  # interpret never called
            self.assertEqual(memo.findings, [])
            self.assertEqual(len(memo.rejected_findings), 1)
            self.assertFalse(memo.citations[0].ok)


class ThinkingRoundTripTest(unittest.TestCase):
    """Finding 1 (round 2): thinking blocks must survive tool round-trips."""

    def test_thinking_blocks_are_replayed_verbatim_with_tool_use(self):
        from context_inspector.agent import run_interpretation, AgentConfig

        with tempfile.TemporaryDirectory() as tmp:
            transcript = _write_transcript(tmp)
            client = FakeClient(
                [
                    _thinking_tool_response("analyze_session", {"path": transcript}),
                    _text_response(FINDING),
                    _text_response(INTERPRET),
                ]
            )
            memo = run_interpretation(
                _manifest([_outlier(transcript)]),
                AgentConfig(notes_dir=Path(tmp), client=client, backoff_seconds=0),
            )
            self.assertEqual(len(memo.findings), 1)
            # the follow-up call must carry the FULL original assistant
            # content — thinking first, tool_use after, signatures intact
            history = client.messages.calls[1]["messages"]
            assistant = history[1]
            self.assertEqual(assistant["role"], "assistant")
            blocks = assistant["content"]
            self.assertEqual(
                [b["type"] for b in blocks], ["thinking", "redacted_thinking", "tool_use"]
            )
            self.assertEqual(blocks[0]["signature"], "sig123")
            self.assertEqual(blocks[2]["name"], "analyze_session")
            # tool result follows as the next user message
            self.assertEqual(history[2]["role"], "user")
            self.assertEqual(history[2]["content"][0]["type"], "tool_result")


class CitationScopeTest(unittest.TestCase):
    """Finding 2 (round 2): citations are validated per-finding evidence scope."""

    def test_cross_session_analyze_evidence_is_out_of_scope(self):
        from context_inspector.agent import run_interpretation, AgentConfig

        with tempfile.TemporaryDirectory() as tmp:
            s1 = _write_transcript(tmp, "session-a.json")
            s2 = _write_transcript(tmp, "session-b.json")
            finding1 = {**FINDING, "pattern": "session-a pattern"}
            finding2 = {**FINDING, "pattern": "session-b pattern"}
            client = FakeClient(
                [
                    _tool_use_response("analyze_session", {"path": s1}),
                    _text_response(finding1),  # cites T1 (session-a evidence)
                    _text_response(finding2),  # cites T1 again — never saw it
                    _text_response(INTERPRET),
                ]
            )
            memo = run_interpretation(
                _manifest([_outlier(s1), _outlier(s2)]),
                AgentConfig(notes_dir=Path(tmp), client=client, backoff_seconds=0),
            )
            self.assertEqual([f.pattern for f in memo.findings], ["session-a pattern"])
            self.assertEqual(len(memo.rejected_findings), 1)
            self.assertIn("scope", memo.rejected_findings[0].rejected_reason)

    def test_shared_run_query_evidence_stays_citable_across_findings(self):
        from context_inspector.agent import run_interpretation, AgentConfig

        with tempfile.TemporaryDirectory() as tmp:
            s1 = _write_transcript(tmp, "session-a.json")
            s2 = _write_transcript(tmp, "session-b.json")
            finding1 = {**FINDING, "pattern": "week-level pattern"}
            finding2 = {**FINDING, "pattern": "same week-level pattern"}
            client = FakeClient(
                [
                    _tool_use_response("run_query", {"name": "whale_table"}),
                    _text_response(finding1),  # cites T1
                    _text_response(finding2),  # cites T1 — legitimately shared
                    _text_response(INTERPRET),
                ]
            )
            memo = run_interpretation(
                _manifest([_outlier(s1), _outlier(s2)]),
                AgentConfig(notes_dir=Path(tmp), client=client, backoff_seconds=0),
            )
            self.assertEqual(len(memo.findings), 2)
            self.assertEqual(memo.rejected_findings, [])


class MemoEvidenceAppendixTest(unittest.TestCase):
    """Finding 3 (round 2): the filed note serializes its own evidence."""

    def test_appendix_maps_ids_to_tool_arguments_and_result(self):
        from context_inspector.agent.memo import Finding, compose_memo, render_markdown
        from context_inspector.agent.tools import ToolReturn

        good = ToolReturn(
            id="T1",
            tool="analyze_session",
            arguments={"path": "/sessions/a.json"},
            content='{"total_tokens": 42}',
            ok=True,
        )
        bad = ToolReturn(
            id="T2",
            tool="read_window",
            arguments={"path": "/sessions/a.json"},
            content='{"error": "budget exhausted"}',
            ok=False,
        )
        finding = Finding(
            session_path="/sessions/a.json",
            pattern="p",
            likely_cause="c",
            prescription="rx",
            citations=["T1"],
            scope={"T1"},
        )
        memo = compose_memo(
            week_id="2026-W42",
            date=GENERATED,
            baseline=True,
            findings=[finding],
            citations=[good, bad],
        )
        text = render_markdown(memo)
        self.assertIn("### T1 — `analyze_session`", text)
        self.assertIn('{"path": "/sessions/a.json"}', text)
        self.assertIn('{"total_tokens": 42}', text)
        self.assertIn("### T2 — `read_window` (error result — not citable)", text)

    def test_appendix_notes_when_no_evidence_was_collected(self):
        from context_inspector.agent.memo import compose_memo, render_markdown

        memo = compose_memo(
            week_id="2026-W42",
            date=GENERATED,
            baseline=True,
            findings=[],
            citations=[],
        )
        self.assertIn(
            "_(no tool evidence collected this week)_", render_markdown(memo)
        )


class DryRunTest(unittest.TestCase):
    """CLI #17: --dry-run composes the memo but never touches the vault."""

    def _script(self, tmp: str) -> list:
        transcript = _write_transcript(tmp)
        return transcript, [
            _tool_use_response("analyze_session", {"path": transcript}),
            _text_response(FINDING),
            _text_response(INTERPRET),
        ]

    def test_dry_run_composes_memo_without_filing(self):
        from context_inspector.agent import AgentConfig, run_interpretation

        with tempfile.TemporaryDirectory() as tmp:
            transcript, script = self._script(tmp)
            notes_dir = Path(tmp) / "notes"
            memo = run_interpretation(
                _manifest([_outlier(transcript)]),
                AgentConfig(
                    notes_dir=notes_dir,
                    dry_run=True,
                    client=FakeClient(script),
                    backoff_seconds=0,
                ),
            )
            self.assertEqual(memo.week_id, "2026-W42")
            self.assertEqual(len(memo.findings), 1)
            self.assertEqual(memo.filed_path, "")
            self.assertFalse(notes_dir.exists(), "dry-run must not create the vault")

    def test_dry_run_does_not_require_notes_dir(self):
        from context_inspector.agent import AgentConfig, run_interpretation

        with tempfile.TemporaryDirectory() as tmp:
            transcript, script = self._script(tmp)
            memo = run_interpretation(
                _manifest([_outlier(transcript)]),
                AgentConfig(
                    notes_dir=None,
                    dry_run=True,
                    client=FakeClient(script),
                    backoff_seconds=0,
                ),
            )
            self.assertEqual(len(memo.findings), 1)


if __name__ == "__main__":
    unittest.main()
