-- name: category_shares
-- description: Token-weighted share per category per source, plus a
--   source='all' grand-total row set.
-- caveats: shares are token-weighted (sum of tokens), not per-session
--   averages — matches sweep.aggregate. Error records excluded.
WITH per_source AS (
    SELECT c.source, category, sum(tokens) AS tokens
    FROM categories c JOIN sessions s USING (path)
    WHERE s.error IS NULL
    GROUP BY c.source, category
), combined AS (
    SELECT source, category, tokens FROM per_source
    UNION ALL
    SELECT 'all', category, sum(tokens) FROM per_source GROUP BY category
)
SELECT source, category, tokens,
       tokens * 1.0 / NULLIF(sum(tokens) OVER (PARTITION BY source), 0) AS share
FROM combined
ORDER BY source, tokens DESC, category;
