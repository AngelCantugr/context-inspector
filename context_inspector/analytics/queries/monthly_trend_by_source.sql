-- name: monthly_trend_by_source
-- description: Sessions, total tokens, median non-empty window per source per calendar month.
-- caveats: month comes from session_date (path-derived Codex date, else file
--   mtime at load time); sessions with no derivable date are excluded.
--   Zero-token sessions are excluded from all aggregates in this query
--   (sessions, total_tokens, median_window), not just the median. Error
--   records excluded.
SELECT source,
       date_trunc('month', session_date)::DATE AS month,
       count(*) AS sessions,
       sum(total_tokens) AS total_tokens,
       median(total_tokens) AS median_window
FROM sessions
WHERE session_date IS NOT NULL AND error IS NULL AND total_tokens > 0
GROUP BY 1, 2 ORDER BY 1, 2;
