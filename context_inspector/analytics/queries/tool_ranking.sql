-- name: tool_ranking
-- description: Per tool — total tokens, share of tool-result tokens, call
--   count, tokens/call — overall (source='all') and per source.
-- caveats: tokens/call is NULL when tool_counts was absent (older sweeps).
--   Mixing old records (no tool_counts) with new ones inflates tokens/call
--   for the same tool, since tokens sum over all records but calls only over
--   counted ones.
--   Shares are of all tool-result tokens within the same source scope.
WITH scoped AS (
    SELECT t.source, t.tool, sum(t.tokens) AS tokens, sum(t.calls) AS calls
    FROM tools t JOIN sessions s USING (path)
    WHERE s.error IS NULL
    GROUP BY t.source, t.tool
), combined AS (
    SELECT source, tool, tokens, calls FROM scoped
    UNION ALL
    SELECT 'all', tool, sum(tokens), sum(calls) FROM scoped GROUP BY tool
)
SELECT source, tool, tokens,
       tokens * 1.0 / sum(tokens) OVER (PARTITION BY source) AS share,
       calls,
       tokens * 1.0 / NULLIF(calls, 0) AS tokens_per_call
FROM combined
ORDER BY source, tokens DESC;
