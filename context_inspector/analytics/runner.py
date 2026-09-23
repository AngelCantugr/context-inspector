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
    sql = (QUERIES_DIR / entry.file).read_text(encoding="utf-8")
    bound = params if params is not None else [p["default"] for p in entry.params]
    cur = con.execute(sql, bound) if bound else con.execute(sql)
    columns = [d[0] for d in cur.description]
    return columns, cur.fetchall()
