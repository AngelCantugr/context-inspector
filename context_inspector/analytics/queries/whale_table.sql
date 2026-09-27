-- name: whale_table
-- description: Top-N sessions by total_tokens with source, dominant category,
--   duplicate count, wasted tokens, signals fired.
-- caveats: dominant category = largest category_tokens entry (ties broken by
--   name). Error records and zero-token sessions excluded. N is a bound
--   parameter (default 10). signals is NULL for old records lacking the
--   field, '' for records with an empty list.
WITH dominant AS (
    SELECT path, category, tokens,
           row_number() OVER (PARTITION BY path ORDER BY tokens DESC, category) AS rn
    FROM categories
)
SELECT row_number() OVER (ORDER BY s.total_tokens DESC, s.path) AS rank,
       s.path, s.source, s.total_tokens,
       d.category AS top_category,
       d.tokens * 1.0 / s.total_tokens AS top_category_share,
       s.n_duplicates, s.wasted_tokens,
       array_to_string(s.signals, '; ') AS signals
FROM sessions s LEFT JOIN dominant d ON d.path = s.path AND d.rn = 1
WHERE s.error IS NULL AND s.total_tokens > 0
ORDER BY s.total_tokens DESC, s.path
LIMIT ?;
