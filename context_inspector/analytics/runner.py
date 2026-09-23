"""Run canonical SQL queries and render results (markdown + JSON)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

QUERIES_DIR = Path(__file__).parent / "queries"


@dataclass
class QueryEntry:
    name: str
    file: str
    description: str
    columns: list[str]
    params: list[dict] = field(default_factory=list)


def load_manifest() -> list[QueryEntry]:
    data = json.loads((QUERIES_DIR / "manifest.json").read_text(encoding="utf-8"))
    return [QueryEntry(**entry) for entry in data["queries"]]


def run_query(con, entry: QueryEntry, params: list | None = None):
    try:
        sql = (QUERIES_DIR / entry.file).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(
            f"query {entry.name!r}: SQL file not found: {QUERIES_DIR / entry.file}"
        ) from None
    bound = params if params is not None else [p["default"] for p in entry.params]
    cur = con.execute(sql, bound) if bound else con.execute(sql)
    columns = [d[0] for d in cur.description]
    return columns, cur.fetchall()


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        s = f"{value:.4g}"
        return s + ".0" if s.lstrip("-").isdigit() else s
    return str(value).replace("|", "\\|")


def render_markdown(entry: QueryEntry, columns: list[str], rows: list[tuple]) -> str:
    """GitHub-flavored markdown table; numeric columns right-aligned."""
    numeric = [
        rows and any(_is_number(r[i]) for r in rows) for i in range(len(columns))
    ]
    lines = [f"# {entry.name}", "", entry.description, ""]
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("|" + "|".join("---:" if numeric[i] else "---" for i in range(len(columns))) + "|")
    for row in rows:
        lines.append("| " + " | ".join(_fmt(v) for v in row) + " |")
    lines.append("")
    return "\n".join(lines)


def run_queries(con, names: list[str], outdir: Path) -> list[str]:
    """Run the named queries, write <name>.md + <name>.json, return summaries."""
    entries = {e.name: e for e in load_manifest()}
    outdir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for name in names:
        entry = entries[name]
        columns, rows = run_query(con, entry)
        (outdir / f"{name}.md").write_text(
            render_markdown(entry, columns, rows), encoding="utf-8"
        )
        (outdir / f"{name}.json").write_text(
            json.dumps([dict(zip(columns, row)) for row in rows],
                       indent=2, default=str),
            encoding="utf-8",
        )
        summaries.append(f"{name}: {len(rows)} rows -> {outdir / (name + '.md')}")
    return summaries
