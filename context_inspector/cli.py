"""CLI entry point: python -m context_inspector {analyze,sweep,query,charts} ..."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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

    p_query = sub.add_parser(
        "query", help="run the canonical SQL query pack over sweep results "
        "(requires the [analytics] extra)"
    )
    p_query.add_argument("sweep_results", help="path to sweep_results.jsonl")
    p_query.add_argument(
        "--query", dest="queries", action="append", metavar="NAME",
        help="run only this query (repeatable; default: all)",
    )
    p_query.add_argument(
        "--out", default="reports/queries", metavar="DIR",
        help="output directory (default: reports/queries)",
    )
    p_query.add_argument(
        "--json", action="store_true",
        help="also print full JSON results to stdout",
    )

    p_charts = sub.add_parser(
        "charts", help="render the L2 figure pack (requires the "
        "[analytics] extra)"
    )
    p_charts.add_argument(
        "--sweep", metavar="PATH",
        help="rerun the needed L1 queries from this sweep_results.jsonl "
        "(default: read existing reports/queries/<name>.json)",
    )
    p_charts.add_argument(
        "--session", metavar="LOG",
        help="session log for the window_growth figure",
    )
    p_charts.add_argument(
        "--from", dest="source", choices=["auto", *SOURCES], default="auto",
        help="input format for --session (default: auto-detect from the path)",
    )
    p_charts.add_argument(
        "--out", default="reports/figures", metavar="DIR",
        help="output directory (default: reports/figures)",
    )
    p_charts.add_argument(
        "--figure", dest="figures", action="append", metavar="NAME",
        help="render only this figure (repeatable; default: all four)",
    )

    args = parser.parse_args(argv)

    if args.command == "query":
        return _run_query(args)
    if args.command == "charts":
        return _run_charts(args)

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


def _run_query(args: argparse.Namespace) -> int:
    try:
        import duckdb

        from .analytics import load_results
        from .analytics.runner import load_manifest, run_queries
    except ImportError:  # duckdb missing — friendly install hint
        print(
            "error: duckdb is not installed. Install it with "
            '`pip install "context-inspector[analytics]"` '
            '(or `uv pip install -e ".[analytics]"` from a clone).',
            file=sys.stderr,
        )
        return 1
    try:
        entries = load_manifest()
    except (OSError, ValueError) as exc:
        print(f"error: could not load query manifest: {exc}", file=sys.stderr)
        return 1
    known = {e.name for e in entries}
    names = args.queries or [e.name for e in entries]
    unknown = [n for n in names if n not in known]
    if unknown:
        print(
            f"error: unknown query {unknown[0]!r} (available: {', '.join(sorted(known))})",
            file=sys.stderr,
        )
        return 2
    try:
        con = load_results(args.sweep_results)
    except (OSError, RuntimeError, duckdb.Error) as exc:
        print(f"error: could not load sweep results: {exc}", file=sys.stderr)
        return 1
    try:
        summaries = run_queries(con, names, Path(args.out))
    except (OSError, RuntimeError, ValueError, duckdb.Error) as exc:
        print(f"error: query failed: {exc}", file=sys.stderr)
        return 1
    for line in summaries:
        print(line)
    if args.json:
        for name in names:
            print((Path(args.out) / f"{name}.json").read_text(encoding="utf-8"))
    return 0


def _run_charts(args: argparse.Namespace) -> int:
    from .charts.runner import FIGURE_NAMES, run_charts

    requested = args.figures or list(FIGURE_NAMES)
    unknown = [f for f in requested if f not in FIGURE_NAMES]
    if unknown:
        print(
            f"error: unknown figure {unknown[0]!r} (available: "
            f"{', '.join(FIGURE_NAMES)})",
            file=sys.stderr,
        )
        return 2
    try:
        rendered = run_charts(
            figures=args.figures,
            sweep=args.sweep,
            session=args.session,
            source=args.source,
            out_dir=Path(args.out),
        )
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for figure, png in rendered:
        print(f"{figure}: {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
