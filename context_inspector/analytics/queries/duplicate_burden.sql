-- name: duplicate_burden
-- description: Sessions with duplicates and tokens in repeated content —
--   count + share of all tokens, per source plus 'all'.
-- caveats: share_of_tokens = wasted_tokens / all tokens (including sessions
--   without duplicates). Error records excluded; zero-token sessions count
--   toward session totals but add no tokens.
WITH per_source AS (
    SELECT source, count(*) AS sessions,
           count(*) FILTER (n_duplicates > 0) AS sessions_with_duplicates,
           sum(wasted_tokens) AS wasted_tokens,
           sum(total_tokens) AS total_tokens
    FROM sessions WHERE error IS NULL
    GROUP BY source
), combined AS (
    SELECT * FROM per_source
    UNION ALL
    SELECT 'all', sum(sessions), sum(sessions_with_duplicates),
           sum(wasted_tokens), sum(total_tokens) FROM per_source
)
SELECT source, sessions, sessions_with_duplicates, wasted_tokens,
       wasted_tokens * 1.0 / total_tokens AS share_of_tokens
FROM combined ORDER BY source;
