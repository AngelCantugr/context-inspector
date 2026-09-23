-- name: bloat_signals_by_tool
-- description: Cross-tab — for sessions where a given signal fired, which
--   tools dominate (tool-result tokens per label × tool).
-- caveats: sessions without tool attribution contribute nothing (e.g. old
--   records). Powers the "what precedes runaway output" narrative. Error
--   records excluded (defensive; sweep_runner never emits signals on error
--   stubs).
SELECT l.label, t.tool, sum(t.tokens) AS tokens,
       count(DISTINCT t.path) AS sessions
FROM signal_labels l
JOIN tools t ON t.path = l.path
JOIN sessions s ON s.path = l.path AND s.error IS NULL
GROUP BY l.label, t.tool
ORDER BY l.label, tokens DESC, t.tool;
