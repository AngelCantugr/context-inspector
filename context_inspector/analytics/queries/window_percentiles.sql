-- name: window_percentiles
-- description: p50/p90/p99 of total_tokens per source.
-- caveats: non-empty sessions only (total_tokens > 0); continuous quantiles
--   (quantile_cont). Error records excluded.
SELECT source,
       quantile_cont(total_tokens, 0.5) AS p50,
       quantile_cont(total_tokens, 0.9) AS p90,
       quantile_cont(total_tokens, 0.99) AS p99
FROM sessions
WHERE error IS NULL AND total_tokens > 0
GROUP BY source ORDER BY source;
