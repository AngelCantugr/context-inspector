-- name: category_outliers
-- description: Sessions where a single category exceeds X% of the window.
-- caveats: threshold is a bound parameter (percent, default 60). Zero-token
--   sessions and error records excluded. Finds e.g. sessions that are 90%
--   one tool result.
SELECT s.path, s.source, c.category, c.tokens AS category_tokens,
       s.total_tokens, c.tokens * 1.0 / s.total_tokens AS share
FROM categories c JOIN sessions s USING (path)
WHERE s.error IS NULL AND s.total_tokens > 0
  AND c.tokens * 100.0 / s.total_tokens > ?
ORDER BY share DESC, s.path;
