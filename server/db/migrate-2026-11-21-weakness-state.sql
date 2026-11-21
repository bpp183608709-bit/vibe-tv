-- 2026-11-21: weakness becomes a persisted state machine (docs/weakness-calc-mapping.md).
--
-- Why the existing table is extended instead of a second one: `weakness` already has the right
-- identity — one row per (device, topic) — and the parent page already reads it. A parallel table
-- would mean two answers to "is this node weak", which is the exact failure the entity was created
-- to stop.
--
-- What changes semantically (not a data migration, because production holds zero rows as of
-- 2026-11-21): detection is now `WA < 0.6` over 15 distinct questions, and closing needs
-- `WA >= 0.8 AND hard_correct >= 1 AND medium_correct >= 3`, counted inside that window. The row
-- therefore stays `active` between 0.6 and 0.8, where the old rule already resolved it.
--
-- Additive only. MySQL 8 has no ADD COLUMN IF NOT EXISTS, so this file runs once — take the dump
-- first (docs/HANDOFF-上线与题库.md).

ALTER TABLE weakness
  ADD COLUMN initial_level TINYINT UNSIGNED NULL AFTER resolved,
  ADD COLUMN detection_snapshot JSON NULL AFTER initial_level,
  ADD COLUMN detection_history JSON NULL AFTER detection_snapshot,
  ADD COLUMN current_window_snapshot JSON NULL AFTER detection_history,
  ADD COLUMN closing_snapshot JSON NULL AFTER current_window_snapshot,
  ADD COLUMN cause_tags JSON NULL AFTER closing_snapshot,
  ADD COLUMN cause_status VARCHAR(24) NULL AFTER cause_tags,
  ADD COLUMN resolved_at DATETIME NULL AFTER cause_status,
  ADD COLUMN reopen_count TINYINT UNSIGNED NOT NULL DEFAULT 0 AFTER resolved_at,
  ADD COLUMN reopened_at DATETIME NULL AFTER reopen_count,
  ADD COLUMN last_practiced_at DATETIME NULL AFTER reopened_at;
