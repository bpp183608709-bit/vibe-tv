-- 2026-11-19: per-ability mastery — mastery_event.ability_tag plus the ability_mastery table.
--
-- Why a second table instead of a second key on topic_mastery: the node row answers "how is this node"
-- (weak list, daily mix quota, the picker's review segment), and every one of those readers wants one
-- row per node. The drill-down and the targeted re-ask want "how is this line of it". Folding both into
-- one table would either quadruple the weak list or force every reader to add `ability_tag IS NULL`.
--
-- The window is per cell. Four lines sharing the node's ten-row window cannot each hold MIN_SAMPLE (3):
-- twelve rows do not fit in ten, and a rolling window forgets the oldest.
--
-- Additive only. MySQL 8 has no ADD COLUMN IF NOT EXISTS, so this file runs once — take the dump first
-- (docs/HANDOFF-上线与题库.md). Safe to run before or after the code deploy: the table is written only
-- by the new code, and the backfill below only reads mastery_event.

-- 1. The ledger carries the line, so a cell's window is one index range instead of a join per answer.
ALTER TABLE mastery_event
  ADD COLUMN ability_tag VARCHAR(16) NULL AFTER question_id,
  ADD KEY idx_event_ability (device_id, topic_id, ability_tag, seq);

-- 2. Backfill it from the question that produced each row. Rows with no question (paper mistakes,
--    parent_adjust, bad_question_void) stay NULL on purpose: a paper mistake has no line.
UPDATE mastery_event e
  JOIN question q ON q.id = e.question_id
   SET e.ability_tag = q.ability_tag
 WHERE e.ability_tag IS NULL
   AND q.ability_tag IS NOT NULL;

-- 3. The table.
CREATE TABLE ability_mastery (
  device_id     CHAR(26)          NOT NULL,
  topic_id      INT UNSIGNED      NOT NULL,
  ability_tag   VARCHAR(16)       NOT NULL,
  score         TINYINT UNSIGNED  NOT NULL DEFAULT 0,
  level         TINYINT UNSIGNED  NOT NULL DEFAULT 0,
  sample_count       TINYINT UNSIGNED  NOT NULL DEFAULT 0,
  weight_sum_x10     SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  correct_weight_x10 SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  acc_x1000          SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  hard_right         TINYINT UNSIGNED  NOT NULL DEFAULT 0,
  seq           INT UNSIGNED      NOT NULL DEFAULT 0,
  weak_since    DATETIME          NULL,
  updated_at    DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (device_id, topic_id, ability_tag),
  KEY idx_ability_weak (device_id, level),
  CONSTRAINT fk_ability_mastery_device FOREIGN KEY (device_id) REFERENCES device (id),
  CONSTRAINT fk_ability_mastery_topic  FOREIGN KEY (topic_id)  REFERENCES topic (id)
) ENGINE=InnoDB;

-- 4. Backfill the cells from history, recomputing the same arithmetic mastery.summarise/level_of use
--    (weights 10/15/20 in tenths, bands 400/600/800, 拓展 needs one hard row right). Bands are written
--    out rather than hidden in a stored function so this file stays readable without the server code.
--
--    Without this the drill-down would read 待测 for every cell until the child answers twelve more
--    times, while the node row it sits under already shows a level — two answers to one question.
INSERT INTO ability_mastery (
  device_id, topic_id, ability_tag, score, level,
  sample_count, weight_sum_x10, correct_weight_x10, acc_x1000, hard_right, seq, updated_at)
SELECT device_id, topic_id, ability_tag,
       LEAST(100, GREATEST(0, ROUND(acc_x1000 / 10))),
       CASE WHEN acc_x1000 < 400 THEN 0
            WHEN acc_x1000 < 600 THEN 1
            WHEN acc_x1000 <= 800 THEN 2
            WHEN hard_right >= 1 THEN 3
            ELSE 2 END,
       sample_count, weight_sum_x10, correct_weight_x10, acc_x1000, hard_right, last_seq, NOW()
  FROM (
    SELECT device_id, topic_id, ability_tag,
           COUNT(*)                                                 AS sample_count,
           SUM(weight_x10)                                          AS weight_sum_x10,
           SUM(IF(is_correct = 1, weight_x10, 0))                   AS correct_weight_x10,
           ROUND(1000 * SUM(IF(is_correct = 1, weight_x10, 0)) / SUM(weight_x10)) AS acc_x1000,
           SUM(IF(is_correct = 1 AND weight_x10 = 20, 1, 0))        AS hard_right,
           MAX(seq)                                                 AS last_seq
      FROM (
        SELECT device_id, topic_id, ability_tag, is_correct, weight_x10, seq,
               ROW_NUMBER() OVER (PARTITION BY device_id, topic_id, ability_tag
                                  ORDER BY seq DESC) AS rn
          FROM mastery_event
         WHERE counted = 1 AND ability_tag IS NOT NULL
      ) ranked
     WHERE rn <= 10
     GROUP BY device_id, topic_id, ability_tag
  ) agg
    ON DUPLICATE KEY UPDATE
      score = VALUES(score), level = VALUES(level), sample_count = VALUES(sample_count),
      weight_sum_x10 = VALUES(weight_sum_x10), correct_weight_x10 = VALUES(correct_weight_x10),
      acc_x1000 = VALUES(acc_x1000), hard_right = VALUES(hard_right), seq = VALUES(seq);

-- Verify (expected: one row per cell that has at least one counted answer with a known line):
--   SELECT COUNT(*), SUM(sample_count >= 3) FROM ability_mastery;
--   SELECT topic_id, ability_tag, level, acc_x1000, sample_count FROM ability_mastery LIMIT 20;
