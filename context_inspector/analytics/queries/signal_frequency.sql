-- name: signal_frequency
-- description: Share of sessions firing each bloat signal, per source.
--   Mirrors the sweep report's signal_frequency block.
-- caveats: labels come from the signal_labels view (same substring rules as
--   sweep.SIGNAL_KEYS). Denominator = valid sessions per source; signals on
--   error records are ignored (there are none by construction).
WITH denom AS (
    SELECT source, count(*) AS n FROM sessions WHERE error IS NULL GROUP BY source
)
SELECT l.source, l.label, d.n AS sessions,
       count(*) * 1.0 / d.n AS share
FROM signal_labels l JOIN denom d USING (source)
JOIN sessions s ON s.path = l.path AND s.error IS NULL
GROUP BY l.source, l.label, d.n
ORDER BY l.source, share DESC;
