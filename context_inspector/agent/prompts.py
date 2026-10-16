"""Prompt builders for the interpretation agent (#15).

The model answers each deep-dive with a single JSON object and the
interpretation stage with one JSON object; parsing lives in graph.py.
"""

from __future__ import annotations

SYSTEM = (
    "You are the interpretation stage of a weekly context-hygiene pipeline. "
    "You analyze why specific agent sessions bloated, using ONLY the "
    "inspect-only tools provided (analyze_session, run_query, read_window). "
    "You never guess: every claim in a finding must be backed by tool "
    "evidence, cited by the evidence ids (T1, T2, ...) shown in tool "
    "results. Reply with raw JSON only — no prose, no markdown fences."
)

DEEP_DIVE_USER = """\
Week context:
{context}

Deep-dive target outlier (rank {rank} of {total}):
{outlier}

Investigate with the tools: analyze_session for the token breakdown,
run_query for week-level patterns, read_window for bounded raw-line peeks
(budgeted — spend them carefully).

Then reply with exactly one JSON object:
{{"pattern": "<what is eating the context>",
  "likely_cause": "<why, grounded in the evidence>",
  "prescription": "<one concrete fix>",
  "citations": ["T1", ...]}}

"citations" MUST list the ids of the tool returns your conclusion is
bound to — findings without valid citations are rejected at compose time.\
"""

INTERPRET_USER = """\
Week context:
{context}

Cited findings so far:
{findings}
{baseline_note}
Reply with exactly one JSON object:
{{"trend_narrative": "<the week-over-week story connecting the findings>",
  "experiment": "<one forward-looking context-hygiene experiment for next week>"}}\
"""

BASELINE_NOTE = """
This is the FIRST weekly run (baseline): there is no previous week to
trend against. Leave "trend_narrative" as an empty string and focus on
the experiment.
"""


def deep_dive_messages(
    context: str, outlier: dict, rank: int, total: int
) -> list[dict]:
    """Fresh Anthropic message list for one outlier's deep dive."""

    import json

    return [
        {"role": "user", "content": DEEP_DIVE_USER.format(
            context=context,
            rank=rank,
            total=total,
            outlier=json.dumps(outlier, indent=2),
        )}
    ]


def interpret_messages(
    context: str, findings: list[dict], baseline: bool
) -> list[dict]:
    """Message list for the trend-narrative / experiment stage."""

    import json

    rendered = "\n".join(
        f"- {json.dumps(f)}" for f in findings
    ) or "(none)"
    return [
        {"role": "user", "content": INTERPRET_USER.format(
            context=context,
            findings=rendered,
            baseline_note=BASELINE_NOTE if baseline else "",
        )}
    ]
