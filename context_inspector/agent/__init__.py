"""Interpretation agent for the weekly context-hygiene pipeline (#15).

langgraph/anthropic are imported only inside this package; the base CLI
never touches them. Imports here are lazy so that importing the package
for its dataclasses does not require the optional extras.
"""

from __future__ import annotations


def __getattr__(name: str):
    if name in (
        "AgentConfig",
        "AgentError",
        "AgentPreflightError",
        "AgentStageError",
        "run_interpretation",
    ):
        from . import graph

        return getattr(graph, name)
    if name in ("Finding", "Memo", "render_markdown"):
        from . import memo

        return getattr(memo, name)
    if name in ("ToolContext", "ToolReturn", "execute_tool"):
        from . import tools

        return getattr(tools, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AgentConfig",
    "AgentError",
    "AgentPreflightError",
    "AgentStageError",
    "Finding",
    "Memo",
    "ToolContext",
    "ToolReturn",
    "execute_tool",
    "render_markdown",
    "run_interpretation",
]
