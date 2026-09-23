"""Load sweep_results.jsonl into DuckDB with an explicit schema.

The JSON is typed by DuckDB itself (``read_json`` with an explicit column
spec — no ``read_json_auto`` guessing, no Python-side expansion of nested
dicts). Records from older sweep versions may lack ``tool_tokens`` /
``tool_counts``, and sweep_runner writes error stubs with only
``source``/``path``/``error``: every missing field loads as NULL.

Public surface: ``load_results(path)`` returns a connection with the
``sessions`` table plus ``categories``, ``tools`` and ``signal_labels``
views ready to query.
"""

from __future__ import annotations

import os
import re
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

_COLUMNS = """{
    source: 'TEXT', path: 'TEXT', total_tokens: 'BIGINT',
    category_tokens: 'MAP(VARCHAR, BIGINT)',
    tool_tokens: 'MAP(VARCHAR, BIGINT)', tool_counts: 'MAP(VARCHAR, BIGINT)',
    n_duplicates: 'INTEGER', wasted_tokens: 'BIGINT',
    signals: 'TEXT[]', error: 'TEXT'
}"""

_DATE_IN_PATH = re.compile(r"(20\d\d)/(\d{2})/(\d{2})")


def _duckdb():
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "duckdb is not installed. Install it with "
            "`pip install \"context-inspector[analytics]\"` "
            "(or `uv pip install -e \".[analytics]\"` from a clone)."
        ) from exc
    return duckdb


def _session_date(path: str) -> date | None:
    """Path-derived date first (Codex embeds YYYY/MM/DD), else file mtime."""
    m = _DATE_IN_PATH.search(path)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    try:
        return date.fromtimestamp(os.path.getmtime(path))
    except OSError:
        return None  # log moved/deleted or fixture path — monthly queries skip it


def load_results(path: str) -> duckdb.DuckDBPyConnection:
    """Load a sweep_results.jsonl file into an in-memory DuckDB database."""
    duckdb = _duckdb()
    if not os.path.exists(path):
        raise FileNotFoundError(f"sweep results file not found: {path}")
    con = duckdb.connect(":memory:")
    escaped = path.replace("'", "''")
    con.execute(
        f"CREATE TABLE sessions AS SELECT * FROM read_json('{escaped}', "
        f"format='newline_delimited', columns={_COLUMNS})"
    )
    con.execute("ALTER TABLE sessions ADD COLUMN session_date DATE")
    rows = con.execute("SELECT rowid, path FROM sessions").fetchall()
    con.executemany(
        "UPDATE sessions SET session_date = ? WHERE rowid = ?",
        [(d, rid) for rid, p in rows if (d := _session_date(p))],
    )
    con.execute(
        """
        CREATE VIEW categories AS
        SELECT path, source, entry.key AS category, entry.value AS tokens
        FROM sessions, unnest(map_entries(category_tokens)) AS u(entry)
        """
    )
    con.execute(
        """
        CREATE VIEW tools AS
        SELECT t.path, t.source, t.tool, t.tokens, c.calls
        FROM (SELECT path, source, e.key AS tool, e.value AS tokens
              FROM sessions, unnest(map_entries(tool_tokens)) AS u(e)) t
        LEFT JOIN (SELECT path, e.key AS tool, e.value AS calls
                   FROM sessions, unnest(map_entries(tool_counts)) AS u(e)) c
        USING (path, tool)
        """
    )
    # Canonical labels mirror sweep.SIGNAL_KEYS substring rules, so query
    # results match the sweep report. DISTINCT mirrors its set semantics.
    # Real sweeps also carry signal strings that match no key (e.g. the
    # single-tool-dominance signal) — the WHERE drops those, like
    # sweep._signal_hits ignoring unmatched strings.
    con.execute(
        """
        CREATE VIEW signal_labels AS
        SELECT DISTINCT path, source, label FROM (
            SELECT path, source,
                CASE
                    WHEN contains(sig, 'tool results') THEN 'tool results >40% of window'
                    WHEN contains(sig, 'system prompt') THEN 'system prompt >25% of window'
                    WHEN contains(sig, 'duplicate')     THEN 'exact duplicates present'
                    WHEN contains(sig, 'alone is')      THEN 'single message >30% of window'
                END AS label
            FROM sessions, unnest(signals) AS u(sig)
        ) WHERE label IS NOT NULL
        """
    )
    return con
