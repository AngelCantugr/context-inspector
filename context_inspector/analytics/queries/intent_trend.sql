-- name: intent_trend
-- description: Per-intent share of tool-result tokens per calendar month.
--   Powers the Serena before/after comparison (gh#12).
-- caveats: only sessions with a derivable session_date (see monthly_trend).
--   Sessions swept before the classifier contribute no intent rows.
SELECT month, intent, tokens,
       tokens * 1.0 / NULLIF(sum(tokens) OVER (PARTITION BY month), 0) AS share
FROM (
    SELECT date_trunc('month', s.session_date)::DATE AS month,
           i.intent, sum(i.tokens) AS tokens
    FROM intents i JOIN sessions s USING (path)
    WHERE s.error IS NULL AND s.session_date IS NOT NULL
    GROUP BY 1, 2
) ORDER BY month, tokens DESC, intent;
