-- Diagnostic: does the meeting_id dedup in behavior_scores.sql.j2 still earn its keep?
--
-- Context: the AutoQA source (l3_asurion_observe_autoqa_evaluations) was suspected of carrying
-- duplicate rows per call, inflating SUM(opportunity). behavior_scores.sql.j2 adds a `dedup` CTE
-- that collapses to ONE row per (agent, event-date, scorecard, behavior, meeting_id) before the
-- weekly SUM. Separately, the score columns were corrected to response_obtainedscore /
-- template_questions_score (the earlier obtainedscore/totalscore were scorecard-level totals that
-- inflated denominators ~15-40x). With the CORRECT columns, this query measures how much residual
-- duplication the dedup actually removes -- so we can decide whether to keep or drop the CTE.
--
-- Read the output per scorecard:
--   rows_per_mb_p50 / _max  -- raw rows per (agent, meeting, behavior). If p50 == max == 1, there
--                              is no duplication and the dedup CTE is a no-op -> safe to remove.
--   raw_denom vs dedup_denom -- weekly opportunity SUM WITHOUT vs WITH the dedup.
--   inflation = raw_denom / dedup_denom -- 1.0 == dedup removes nothing; >1 == it still matters.
--
-- Dialect: Trino/Presto (matches extraction/sql/*.j2). Params mirror extraction/configs/
-- extract_run.yaml for the current window; edit to re-run for another window.
--   window = 2026-07-18 .. 2026-08-28

WITH roster AS (
  SELECT DISTINCT a.expert_id AS agent_id
  FROM hive.care.expert_performance_metrics a
  WHERE CAST(a."date" AS DATE) BETWEEN DATE '2026-07-18' AND DATE '2026-08-28'
    AND LOWER(a.icp_client) IN (
      'mob-at&t','mob-verizon','pss-at&t','pss-at&t nac','pss-verizon','xbox','mcafee'
    )
),
scored AS (   -- raw evaluation rows (no dedup); scorecards mirror observe_scorecards
  SELECT
    b.partner_agent_id                            AS agent_id,
    b.meeting_id                                  AS meeting_id,
    b.template_questions_name                     AS scorecard_name,
    b.template_questions_phrase                   AS behavior_raw,
    CAST(b.template_questions_score AS DOUBLE)    AS opp
  FROM hive.care.l3_asurion_observe_autoqa_evaluations b
  JOIN roster r ON r.agent_id = b.partner_agent_id
  WHERE CAST(b."created_at" AS DATE) BETWEEN DATE '2026-07-18' AND DATE '2026-08-28'
    AND b.template_questions_name IN (
      'HEROES Auto Scorecard','HEROES Auto QA Reporting - VZW',
      'Customer Sentiment Scorecard V1','Advanced Discovery Call Flow V1'
    )
),
per_mb AS (   -- one group per (agent, meeting, behavior): row count + deduped opportunity (MAX)
  SELECT agent_id, meeting_id, scorecard_name, behavior_raw,
         COUNT(*) AS rows_per_group,
         MAX(opp) AS dedup_opp
  FROM scored
  GROUP BY agent_id, meeting_id, scorecard_name, behavior_raw
),
raw_denom AS (
  SELECT scorecard_name, SUM(opp) AS raw_denom FROM scored GROUP BY scorecard_name
)
SELECT
  m.scorecard_name,
  COUNT(*)                                              AS meeting_behavior_groups,
  approx_percentile(m.rows_per_group, 0.50)             AS rows_per_mb_p50,
  approx_percentile(m.rows_per_group, 0.95)             AS rows_per_mb_p95,
  MAX(m.rows_per_group)                                 AS rows_per_mb_max,
  ROUND(r.raw_denom, 1)                                 AS raw_denom,
  ROUND(SUM(m.dedup_opp), 1)                            AS dedup_denom,
  ROUND(r.raw_denom / NULLIF(SUM(m.dedup_opp), 0), 3)   AS inflation_factor
FROM per_mb m
JOIN raw_denom r ON r.scorecard_name = m.scorecard_name
GROUP BY m.scorecard_name, r.raw_denom
ORDER BY m.scorecard_name
