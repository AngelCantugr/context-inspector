"""CLI entry point: python -m context_inspector {analyze,sweep} ..."""

from __future__ import annotations

import argparse
import json
import sys

from .adapters import SOURCES, load_any
from .analyzer import analyze, render_text, to_dict
from .estimator import get_tokenizer
from .sweep import render_sweep, run_sweep, to_markdown


def _add_tokenizer_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--tokenizer",
        choices=["heuristic", "tiktoken"],
        default="heuristic",
        help="token counting method (default: heuristic, stdlib-only; "
        "tiktoken gives real counts but requires the tiktoken package)",
    )
    p.add_argument(
        "--encoding",
        default="o200k_base",
        help="tiktoken encoding (default: o200k_base)",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="context_inspector",
        description="Inspect token usage and bloat in agent chat transcripts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="analyze a transcript JSON file")
    p_analyze.add_argument(
        "transcript",
        help="OpenAI-format messages JSON, or a session log from "
        "Claude Code / Codex CLI / Kimi Code",
    )
    p_analyze.add_argument(
        "--from",
        dest="source",
        choices=["auto", *SOURCES],
        default="auto",
        help="input format (default: auto-detect from the path)",
    )
    p_analyze.add_argument("--json", action="store_true", help="machine-readable output")
    _add_tokenizer_args(p_analyze)

    p_sweep = sub.add_parser(
        "sweep", help="analyze every local session from all supported harnesses"
    )
    p_sweep.add_argument("--md", action="store_true", help="markdown output")
    p_sweep.add_argument("--save", metavar="PATH", help="also write the report to a file")
    _add_tokenizer_args(p_sweep)

    args = parser.parse_args(argv)

    try:
        tokenizer = get_tokenizer(args.tokenizer, args.encoding)
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.command == "sweep":
        results, agg = run_sweep(tokenizer=tokenizer)
        report_md = to_markdown(results, agg)
        if args.md:
            print(report_md)
        else:
            print(render_sweep(results, agg))
        if args.save:
            from pathlib import Path

            save_path = Path(args.save)
            if save_path.parent != Path("."):
                save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(report_md, encoding="utf-8")
            print(f"\nreport saved: {args.save}")
        return 0

    try:
        messages, source = load_any(
            args.transcript, None if args.source == "auto" else args.source
        )
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: could not load transcript: {exc}", file=sys.stderr)
        return 1

    try:
        report = analyze(messages, tokenizer=tokenizer)
    except (AttributeError, TypeError, ValueError) as exc:
        print(f"error: could not analyze transcript: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(to_dict(report), indent=2))
    else:
        origin = f"{source} session log" if source else "openai json"
        print(f"input format: {origin} · tokenizer: {args.tokenizer}\n")
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
