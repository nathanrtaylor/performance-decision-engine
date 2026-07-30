-- Diagnostic: does an AT&T-scored sentiment/QA scorecard exist in the source that the
-- behavior_scores extraction is silently dropping?
--
-- Why this exists: behavior_scores.sql.j2 filters observe rows to a hardcoded scorecard
-- allow-list (observe_scorecards in extract_run.yaml):
--     'HEROES Auto Scorecard', 'HEROES Auto QA Reporting - VZW', 'Customer Sentiment Scorecard V1'
-- In the wk6 extract, the two sentiment-bearing scorecards produced ZERO rows for the AT&T
-- cohorts (mob-at&t, pss-at&t). This query bypasses that allow-list to distinguish:
--   (a) AT&T genuinely has no sentiment scorecard in the source  -> nothing to fix, split stays VZW-only
--   (b) AT&T has one under a name NOT in the allow-list (e.g. a '...- ATT' variant) -> add it to
--       observe_scorecards and re-extract.
--
-- Dialect: Trino/Presto (matches extraction/sql/*.j2). Values below mirror extraction/configs/
-- extract_run.yaml for the wk6 window; edit the 3 params to re-run for another window.
--
--   source_schema = hive.care   epm_table = expert_performance_metrics
--   observe_table = l3_asurion_observe_behaviors_scores
--   window        = 2026-06-20 .. 2026-07-31

-- =====================================================================================
-- Q1: every scorecard each cohort was ACTUALLY scored on (no scorecard filter), with a
--     flag for whether the current extraction allow-list already captures it.
-- =====================================================================================
WITH epm AS (
  SELECT
    a.expert_id AS agent_id,
    LOWER(a.icp_client) AS icp_client,
    CAST(a."date" AS DATE) AS dt
  FROM hive.care.expert_performance_metrics a
  WHERE CAST(a."date" AS DATE) BETWEEN DATE '2026-06-20' AND DATE '2026-07-31'
    AND LOWER(a.icp_client) IN ('pss-verizon','pss-at&t','mob-verizon','mob-at&t')
),
observe_all AS (   -- NOTE: deliberately NO template_questions_name filter here
  SELECT
    b.expert_id AS agent_id,
    CAST(b."date" AS DATE) AS dt,
    b.template_questions_name AS scorecard_name,
    b.behavior_name AS behavior
  FROM hive.care.l3_asurion_observe_behaviors_scores b
  WHERE CAST(b."date" AS DATE) BETWEEN DATE '2026-06-20' AND DATE '2026-07-31'
)
SELECT
  e.icp_client,
  o.scorecard_name,
  COUNT(*)                        AS score_rows,
  COUNT(DISTINCT e.agent_id)      AS experts,
  COUNT(DISTINCT o.behavior)      AS behaviors,
  CASE WHEN o.scorecard_name IN (
         'HEROES Auto Scorecard',
         'HEROES Auto QA Reporting - VZW',
         'Customer Sentiment Scorecard V1'
       ) THEN 'in_current_extract' ELSE 'DROPPED_BY_FILTER' END AS extract_status
FROM epm e
JOIN observe_all o                 -- INNER: only scorecards the cohort was actually scored on
  ON e.agent_id = o.agent_id
 AND e.dt       = o.dt
GROUP BY 1, 2
ORDER BY e.icp_client, score_rows DESC;

-- =====================================================================================
-- Q2: focus on the AT&T cohorts + sentiment/QA-looking scorecards, listing exact
--     behavior names. If rows come back with extract_status = 'DROPPED_BY_FILTER', that
--     scorecard_name is the gap: add it verbatim to observe_scorecards and re-extract.
--     (Run separately; some engines don't allow two statements per submission.)
-- =====================================================================================
-- WITH epm AS (
--   SELECT a.expert_id AS agent_id, LOWER(a.icp_client) AS icp_client, CAST(a."date" AS DATE) AS dt
--   FROM hive.care.expert_performance_metrics a
--   WHERE CAST(a."date" AS DATE) BETWEEN DATE '2026-06-20' AND DATE '2026-07-31'
--     AND LOWER(a.icp_client) IN ('pss-at&t','mob-at&t')
-- )
-- SELECT e.icp_client, b.template_questions_name AS scorecard_name, b.behavior_name AS behavior,
--        COUNT(*) AS score_rows, COUNT(DISTINCT e.agent_id) AS experts
-- FROM epm e
-- JOIN hive.care.l3_asurion_observe_behaviors_scores b
--   ON b.expert_id = e.agent_id AND CAST(b."date" AS DATE) = e.dt
-- WHERE CAST(b."date" AS DATE) BETWEEN DATE '2026-06-20' AND DATE '2026-07-31'
--   AND ( LOWER(b.template_questions_name) LIKE '%sentiment%'
--      OR LOWER(b.template_questions_name) LIKE '%qa%'
--      OR LOWER(b.template_questions_name) LIKE '%att%'
--      OR LOWER(b.template_questions_name) LIKE '%at&t%' )
-- GROUP BY 1, 2, 3
-- ORDER BY e.icp_client, score_rows DESC;
