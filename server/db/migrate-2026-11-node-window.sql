-- 2026-11-18: diagnostic nodes + rolling-window mastery.
--
-- Brings a database created before the weighted model up to the shape in schema.sql. Every statement
-- is additive; nothing is dropped or rewritten. MySQL 8 has no ADD COLUMN IF NOT EXISTS, so this file
-- runs once — take the dump first (docs/HANDOFF-上线与题库.md, 回滚).
--
-- Order matters only for readability: `AFTER` keeps the columns where schema.sql declares them, so a
-- later `SHOW CREATE TABLE` diff against a fresh schema.sql comes out empty.

-- `code` comes first because every other new column is positioned relative to it: the question files
-- and docs/data_sources.md name a node by 'math-6up-d01', and the importer resolves by that string
-- before it falls back to (unit_id, ordinal).
ALTER TABLE topic
  ADD COLUMN code VARCHAR(24) NULL AFTER ordinal,
  ADD UNIQUE KEY uk_topic_code (code);

ALTER TABLE topic
  ADD COLUMN micro_nodes_json JSON NULL AFTER summary_zh,
  ADD COLUMN prereq_json      JSON NULL AFTER micro_nodes_json,
  ADD COLUMN knowledge_type   VARCHAR(16) NULL AFTER prereq_json;

ALTER TABLE question
  ADD COLUMN ability_tag VARCHAR(16) NULL AFTER angle;

ALTER TABLE daily_state
  ADD COLUMN mix_deepest TINYINT UNSIGNED NULL AFTER mix_n;

ALTER TABLE topic_mastery
  ADD COLUMN sample_count       TINYINT UNSIGNED  NOT NULL DEFAULT 0  AFTER ready_qty,
  ADD COLUMN weight_sum_x10     SMALLINT UNSIGNED NOT NULL DEFAULT 0  AFTER sample_count,
  ADD COLUMN correct_weight_x10 SMALLINT UNSIGNED NOT NULL DEFAULT 0  AFTER weight_sum_x10,
  ADD COLUMN acc_x1000          SMALLINT UNSIGNED NOT NULL DEFAULT 0  AFTER correct_weight_x10,
  ADD COLUMN hard_right         TINYINT UNSIGNED  NOT NULL DEFAULT 0  AFTER acc_x1000,
  ADD COLUMN seq                INT UNSIGNED      NOT NULL DEFAULT 0  AFTER hard_right,
  ADD COLUMN bias               SMALLINT          NOT NULL DEFAULT 0  AFTER seq,
  ADD COLUMN weak_since         DATETIME          NULL                AFTER total_right;

ALTER TABLE mastery_event
  ADD COLUMN is_correct   TINYINT(1)         NULL              AFTER score_after,
  ADD COLUMN difficulty   TINYINT UNSIGNED   NULL              AFTER is_correct,
  ADD COLUMN weight_x10   SMALLINT UNSIGNED  NULL              AFTER difficulty,
  ADD COLUMN counted      TINYINT(1)         NOT NULL DEFAULT 0 AFTER weight_x10,
  ADD COLUMN acc_after    SMALLINT UNSIGNED  NULL              AFTER counted,
  ADD COLUMN sample_after TINYINT UNSIGNED   NULL              AFTER acc_after,
  ADD COLUMN seq          INT UNSIGNED       NOT NULL DEFAULT 0 AFTER sample_after,
  ADD KEY idx_event_window (device_id, topic_id, seq);
