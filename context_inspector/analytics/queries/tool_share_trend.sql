-- name: tool_share_trend
-- description: Per-tool share of tool-result tokens per calendar month.
-- caveats: only sessions with a derivable session_date (see monthly_trend).
--   Detects e.g. shell output growing over time. Error records excluded.
SELECT month, tool, tokens,
       tokens * 1.0 / sum(tokens) OVER (PARTITION BY month) AS share
FROM (
    SELECT date_trunc('month', s.session_date)::DATE AS month,
           t.tool, sum(t.tokens) AS tokens
    FROM tools t JOIN sessions s USING (path)
    WHERE s.error IS NULL AND s.session_date IS NOT NULL
    GROUP BY 1, 2
) ORDER BY month, tokens DESC;
