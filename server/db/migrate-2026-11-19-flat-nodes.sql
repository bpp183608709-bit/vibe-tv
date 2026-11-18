-- 2026-11-19: flat nodes, stable aggregate ids, question.unit_id emptied, weakness table.
--
-- Brings a database created before this revision up to schema.sql. Additive apart from one MODIFY
-- (unit_id becomes nullable) and one index swap. MySQL 8 has no ADD COLUMN IF NOT EXISTS, so this file
-- runs once — take the dump first (docs/HANDOFF-上线与题库.md, 回滚).
--
-- Order matters here, unlike the 2026-11 migration: `code` and `subject_id` are backfilled after they
-- exist, and `unit_id` is emptied only after the picker no longer reads it (deploy the code first).

-- 1. Stable ids for the aggregate nodes, and the chapter ordinal the diagnostic nodes now carry.
ALTER TABLE unit
  ADD COLUMN code VARCHAR(24) NULL AFTER ordinal,
  ADD UNIQUE KEY uk_unit_code (code);

ALTER TABLE topic
  ADD COLUMN chapter_order TINYINT UNSIGNED NOT NULL DEFAULT 0 AFTER ordinal,
  ADD COLUMN source_ids_json JSON NULL AFTER micro_nodes_json;

-- 2. A question hangs from a diagnostic node. unit_id stops being a pointer and becomes empty.
ALTER TABLE question
  MODIFY COLUMN unit_id SMALLINT UNSIGNED NULL,
  ADD COLUMN code VARCHAR(40) NULL AFTER topic_id,
  ADD COLUMN subject_id SMALLINT UNSIGNED NULL AFTER code,
  ADD UNIQUE KEY uk_question_code (code),
  DROP INDEX idx_question_pick,
  ADD KEY idx_question_pick (topic_id, difficulty, angle, id);

-- 3. The answer record explains itself; the snapshot keeps its drill-down.
ALTER TABLE quiz_answer
  ADD COLUMN diagnostic_node_id VARCHAR(24) NULL AFTER is_correct,
  ADD COLUMN ability_tag VARCHAR(16) NULL AFTER diagnostic_node_id;

ALTER TABLE topic_mastery
  ADD COLUMN ability_breakdown_json JSON NULL AFTER weak_since;

ALTER TABLE mastery_event
  ADD COLUMN question_id INT UNSIGNED NULL AFTER topic_id;

-- 4. The weak list as an entity.
CREATE TABLE weakness (
  weakness_id    CHAR(26)      NOT NULL,
  device_id      CHAR(26)      NOT NULL,
  topic_id       INT UNSIGNED  NOT NULL,
  diagnostic_node_id VARCHAR(24) NOT NULL,
  mastery_level  TINYINT UNSIGNED NOT NULL,
  weighted_accuracy SMALLINT UNSIGNED NOT NULL,
  sample_count   TINYINT UNSIGNED NOT NULL,
  evidence_question_ids JSON   NULL,
  parent_aggregate_node_id VARCHAR(24) NOT NULL,
  parent_aggregate_name    VARCHAR(64) NOT NULL,
  resolved       TINYINT(1)    NOT NULL DEFAULT 0,
  opened_at      DATETIME      NOT NULL,
  updated_at     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (weakness_id),
  UNIQUE KEY uk_weakness (device_id, topic_id),
  KEY idx_weakness_open (device_id, resolved, mastery_level),
  CONSTRAINT fk_weakness_device FOREIGN KEY (device_id) REFERENCES device (id),
  CONSTRAINT fk_weakness_topic  FOREIGN KEY (topic_id)  REFERENCES topic (id)
) ENGINE=InnoDB;

-- 5. Backfill. Everything here is derived from rows that already exist, so it is safe to re-run.
UPDATE topic t JOIN unit u ON u.id = t.unit_id SET t.chapter_order = u.ordinal WHERE t.chapter_order = 0;

UPDATE unit u
  JOIN textbook b ON b.id = u.textbook_id
  JOIN subject s  ON s.id = b.subject_id
   SET u.code = CONCAT(s.code, '-6up-u', LPAD(u.ordinal, 2, '0'))
 WHERE u.code IS NULL;

UPDATE question q
  JOIN textbook b ON b.id = q.textbook_id
   SET q.subject_id = b.subject_id
 WHERE q.subject_id IS NULL;

UPDATE quiz_answer a
  JOIN question q ON q.id = a.question_id
  JOIN topic t    ON t.id = q.topic_id
   SET a.diagnostic_node_id = t.code,
       a.ability_tag = q.ability_tag
 WHERE a.diagnostic_node_id IS NULL;

-- Emptied last: the old server still joins `unit` on this column.
UPDATE question SET unit_id = NULL WHERE unit_id IS NOT NULL;

-- 6. Rebuild the weak list from whatever topic_mastery already says, so the page is not empty until
-- the child answers again. The columns mirror mastery.is_weak: measured (>=3) and level <= 1.
INSERT INTO weakness (weakness_id, device_id, topic_id, diagnostic_node_id, mastery_level,
                      weighted_accuracy, sample_count, evidence_question_ids,
                      parent_aggregate_node_id, parent_aggregate_name, resolved, opened_at)
SELECT REPLACE(UUID(), '-', ''), m.device_id, m.topic_id, t.code, m.level,
       m.acc_x1000, m.sample_count, NULL,
       u.code, u.name_zh, 0, COALESCE(m.weak_since, m.updated_at)
  FROM topic_mastery m
  JOIN topic t ON t.id = m.topic_id
  JOIN unit  u ON u.id = t.unit_id
 WHERE m.level <= 1 AND m.sample_count >= 3;
