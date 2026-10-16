"""Memo data model and deterministic markdown rendering (#15).

``validate_findings`` is the citation gate: a finding survives only if
it cites at least one *successful* tool return inside its own evidence
scope. The graph applies the gate BEFORE interpretation (rejected
findings never reach the narrative stage), and ``compose_memo`` applies
it again at assembly time, so the filed memo can never contain a finding
the model invented without evidence it was actually shown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .tools import ToolReturn


@dataclass
class Finding:
    """One deep-dive outcome: pattern, cause, prescription, citations.

    ``scope`` is the set of evidence ids that were actually supplied to
    this finding's deep dive: the tool returns produced during that deep
    dive plus any run_query returns from the run (query-pack evidence is
    week-level and legitimately spans sessions). Session-specific evidence
    (analyze_session / read_window) from OTHER deep dives is NOT in scope.
    The citation gate rejects citations outside a finding's scope, so a
    finding can never ride on evidence it never saw.
    """

    session_path: str
    pattern: str
    likely_cause: str
    prescription: str
    citations: list[str] = field(default_factory=list)
    scope: set[str] = field(default_factory=set)
    rejected_reason: str = ""


@dataclass
class Memo:
    """The composed weekly memo: kept findings plus rejected ones."""

    week_id: str  # e.g. "2026-W42"
    date: str  # ISO date the manifest was generated
    baseline: bool
    findings: list[Finding] = field(default_factory=list)
    rejected_findings: list[Finding] = field(default_factory=list)
    trend_narrative: str = ""
    experiment: str = ""
    citations: list[ToolReturn] = field(default_factory=list)
    filed_path: str = ""


def validate_findings(
    findings: list[Finding], citations: list[ToolReturn]
) -> tuple[list[Finding], list[Finding]]:
    """Split findings into kept vs rejected against the citation registry.

    A finding is kept only when at least one of its citations resolves to
    a *successful* tool return that is also inside the finding's evidence
    scope — i.e. evidence actually supplied to its deep dive. Error
    results (missing files, exhausted budgets, unknown tools) and
    session-specific evidence from other deep dives do not count.
    """

    known_ok = {c.id for c in citations if c.ok}
    kept: list[Finding] = []
    rejected: list[Finding] = []
    for finding in findings:
        valid = [
            c
            for c in finding.citations
            if c in known_ok and c in finding.scope
        ]
        if valid:
            finding.citations = valid
            kept.append(finding)
        else:
            finding.rejected_reason = (
                "no citation bound to successful tool return in this "
                "finding's evidence scope"
            )
            rejected.append(finding)
    return kept, rejected


def compose_memo(
    *,
    week_id: str,
    date: str,
    baseline: bool,
    findings: list[Finding],
    citations: list[ToolReturn],
    trend_narrative: str = "",
    experiment: str = "",
    rejected_findings: list[Finding] | None = None,
) -> Memo:
    """Validate findings against the citation registry and build the memo.

    ``rejected_findings`` carries findings already screened out by an
    earlier gate (the graph gates before interpretation); the registry
    check runs again here so direct callers get the same guarantee.
    """

    kept, rejected = validate_findings(findings, citations)
    return Memo(
        week_id=week_id,
        date=date,
        baseline=baseline,
        findings=kept,
        rejected_findings=list(rejected_findings or []) + rejected,
        trend_narrative=trend_narrative,
        experiment=experiment,
        citations=citations,
    )


def render_markdown(memo: Memo) -> str:
    """Fixed-section markdown note with deterministic frontmatter."""

    lines = [
        "---",
        "type: report",
        f"date: {memo.date}",
        f"title: Context hygiene weekly {memo.week_id}",
    ]
    if not memo.baseline:
        year, _, week = memo.week_id.partition("-W")
        prev_year, prev_week = _prev_week(int(year), int(week))
        lines.append(
            f"prev: context-hygiene-weekly-{prev_year}-W{prev_week:02d}"
        )
    lines.extend(["---", ""])
    title = f"# Context hygiene weekly {memo.week_id}"
    lines.append(f"{title} — baseline week" if memo.baseline else title)
    lines.append("")

    lines.append("## Findings")
    lines.append("")
    if not memo.findings:
        lines.append("_No citable findings this week._")
    for i, f in enumerate(memo.findings, 1):
        lines.append(f"### {i}. {f.pattern}")
        lines.append("")
        lines.append(f"- **Session:** `{f.session_path}`")
        lines.append(f"- **Likely cause:** {f.likely_cause}")
        lines.append(f"- **Prescription:** {f.prescription}")
        lines.append(f"- **Evidence:** {', '.join(f.citations)}")
        lines.append("")

    if memo.rejected_findings:
        lines.append("## Rejected findings (uncited)")
        lines.append("")
        for f in memo.rejected_findings:
            lines.append(
                f"- _{f.pattern}_ — {f.rejected_reason}"
            )
        lines.append("")

    if not memo.baseline:
        lines.append("## Trend narrative")
        lines.append("")
        lines.append(memo.trend_narrative or "_No trend narrative produced._")
        lines.append("")

    lines.append("## Next week's experiment")
    lines.append("")
    lines.append(memo.experiment or "_No experiment proposed._")
    lines.append("")

    lines.append("## Evidence")
    lines.append("")
    if not memo.citations:
        lines.append("_(no tool evidence collected this week)_")
    for c in memo.citations:
        status = "" if c.ok else " (error result — not citable)"
        lines.append(f"### {c.id} — `{c.tool}`{status}")
        lines.append("")
        lines.append(f"Arguments: `{json.dumps(c.arguments, sort_keys=True)}`")
        lines.append("")
        lines.append("```json")
        lines.append(c.content)
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def _prev_week(year: int, week: int) -> tuple[int, int]:
    if week > 1:
        return year, week - 1
    return year - 1, 52
