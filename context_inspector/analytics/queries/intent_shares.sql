-- name: intent_shares
-- description: Per intent — total tokens, share of tool-result tokens, call
--   count, tokens/call — overall (source='all') and per source.
-- caveats: tokens/call is NULL when intent_counts was absent (older sweeps) —
--   same mixing caveat as tool_ranking. Shares are of classified intent
--   tokens within the source scope; they sum to 1 only when every tool-result
--   token classified (true for post-classifier sweeps). Sessions swept before
--   the classifier contribute no intent rows at all.
WITH scoped AS (
    SELECT i.source, i.intent, sum(i.tokens) AS tokens, sum(i.calls) AS calls
    FROM intents i JOIN sessions s USING (path)
    WHERE s.error IS NULL
    GROUP BY i.source, i.intent
), combined AS (
    SELECT source, intent, tokens, calls FROM scoped
    UNION ALL
    SELECT 'all', intent, sum(tokens), sum(calls) FROM scoped GROUP BY intent
)
SELECT source, intent, tokens,
       tokens * 1.0 / NULLIF(sum(tokens) OVER (PARTITION BY source), 0) AS share,
       calls,
       tokens * 1.0 / NULLIF(calls, 0) AS tokens_per_call
FROM combined
ORDER BY source, tokens DESC, intent;
