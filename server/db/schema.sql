-- Quiz-for-time: content tree, questions, attempts, tickets, devices.
-- Spec: docs/QUIZ.md. MySQL 8.0 / InnoDB / utf8mb4.
--
-- Two halves that never share a transaction:
--   * the content half (subject ... question_option) is written by the import tools, read-only at runtime;
--   * the runtime half (device ... ticket) is written by the API.
-- day_key is a DATE in the *client's* local day (the TV's timezone), produced by the client's
-- ParentalStore.dayKey(). Do not recompute it from NOW() on the server: the TV may be in another zone,
-- and a ticket is only redeemable on the day it was issued.

SET NAMES utf8mb4;

-- ---------------------------------------------------------------- content

CREATE TABLE subject (
  id          TINYINT UNSIGNED NOT NULL,
  code        VARCHAR(16)      NOT NULL,          -- 'math' | 'chinese' | 'english'
  name_zh     VARCHAR(32)      NOT NULL,
  sort_order  TINYINT UNSIGNED NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_subject_code (code)
) ENGINE=InnoDB;

-- One row per (subject, publisher, grade, term) = one 册. The textbook is what the topic tree hangs
-- from; a wrong publisher invalidates the whole subject's bank (docs/QUIZ.md §5).
CREATE TABLE textbook (
  id          SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
  subject_id  TINYINT UNSIGNED  NOT NULL,
  publisher   VARCHAR(32)       NOT NULL,         -- 'pep' | 'xiangshao' (zh + math are both pep)
  grade       TINYINT UNSIGNED  NOT NULL,         -- 1..6
  term        ENUM('up','down') NOT NULL,         -- 上册 / 下册
  title_zh    VARCHAR(64)       NOT NULL,
  is_active   TINYINT(1)        NOT NULL DEFAULT 1,
  PRIMARY KEY (id),
  UNIQUE KEY uk_textbook (subject_id, publisher, grade, term)
) ENGINE=InnoDB;

CREATE TABLE unit (
  id           SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
  textbook_id  SMALLINT UNSIGNED NOT NULL,
  ordinal      TINYINT UNSIGNED  NOT NULL,        -- 1-based position in the table of contents
  name_zh      VARCHAR(64)       NOT NULL,
  -- Which school week this unit is normally taught in. Drives the calendar-based auto-advance (C3-B);
  -- the parent only ever overrides it.
  week_start   TINYINT UNSIGNED  NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_unit (textbook_id, ordinal),
  CONSTRAINT fk_unit_textbook FOREIGN KEY (textbook_id) REFERENCES textbook (id)
) ENGINE=InnoDB;

-- The atom everything joins on: wrong-answer stats, similar-question generation, progress matching.
CREATE TABLE topic (
  id           INT UNSIGNED      NOT NULL AUTO_INCREMENT,
  unit_id      SMALLINT UNSIGNED NOT NULL,
  ordinal      TINYINT UNSIGNED  NOT NULL,
  name_zh      VARCHAR(64)       NOT NULL,        -- '分数乘整数'
  -- One line the parent reads in the weekly report; not shown to the child (C13 shows the name only).
  summary_zh   VARCHAR(255)      NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_topic (unit_id, ordinal),
  CONSTRAINT fk_topic_unit FOREIGN KEY (unit_id) REFERENCES unit (id)
) ENGINE=InnoDB;

CREATE TABLE question (
  id               INT UNSIGNED      NOT NULL AUTO_INCREMENT,
  textbook_id      SMALLINT UNSIGNED NOT NULL,
  -- Denormalised from the primary topic so "give me the next question of unit 3" is one index scan.
  unit_id          SMALLINT UNSIGNED NOT NULL,
  topic_id         INT UNSIGNED      NOT NULL,     -- the primary topic (E4: one label, ~10% multi)
  angle            ENUM('recall','understand','apply') NOT NULL,
  difficulty       TINYINT UNSIGNED  NOT NULL,     -- 1 easy, 2 medium, 3 hard
  qtype            ENUM('choice','fill') NOT NULL, -- E8: figures only as choice/fill
  stem             TEXT              NOT NULL,     -- <= 120 chars, enforced by the importer
  stem_len         SMALLINT UNSIGNED NOT NULL,     -- characters, so the importer can reject a wall of text
  -- Fill-in answers, already normalised (trim, full-width -> half-width, case-folded for latin).
  -- Empty for choices: their answer lives in question_option, which is what gets downloaded (D9).
  -- Only the canonical form; accepted equivalents are rows in question_answer.
  answer_norm      VARCHAR(64)       NULL,
  explanation_zh   TEXT              NULL,         -- shown after submission, never before
  -- draft: not servable. sympy_ok: the maths was recomputed and matched (E6). manual_ok: a human read it.
  -- rejected: do not serve, ever.
  verify_status    ENUM('draft','sympy_ok','manual_ok','rejected') NOT NULL DEFAULT 'draft',
  verify_note      VARCHAR(255)      NULL,         -- what sympy said, or who checked it
  distractor_score TINYINT UNSIGNED  NULL,         -- 0..100, quality of the wrong options
  source           ENUM('ai','manual') NOT NULL DEFAULT 'ai',
  -- Template + parameters identify a generated question. Two questions from the same template with
  -- different parameters are different questions to the child but the same skill, so de-duplication
  -- keys on this pair, never on the stem text (docs/QUIZ.md §6).
  template_key     VARCHAR(32)       NOT NULL DEFAULT 'manual',
  param_hash       CHAR(16)          NOT NULL DEFAULT '',  -- hex of the sampled parameters
  created_at       DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,
  retired_at       DATETIME          NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_question_dedup (topic_id, template_key, param_hash),
  KEY idx_question_pick (unit_id, difficulty, angle, id),
  KEY idx_question_topic (topic_id, difficulty),
  CONSTRAINT fk_question_textbook FOREIGN KEY (textbook_id) REFERENCES textbook (id),
  CONSTRAINT fk_question_topic FOREIGN KEY (topic_id) REFERENCES topic (id)
) ENGINE=InnoDB;

CREATE TABLE question_option (
  question_id  INT UNSIGNED     NOT NULL,
  label        CHAR(1)          NOT NULL,          -- A..D
  text         VARCHAR(255)     NOT NULL,
  is_correct   TINYINT(1)       NOT NULL DEFAULT 0,
  PRIMARY KEY (question_id, label),
  CONSTRAINT fk_option_question FOREIGN KEY (question_id) REFERENCES question (id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Accepted answers for a fill-in question. A sixth-grader writing 0.75, 3/4 or "四分之三" is giving the
-- same answer; a single answer_norm column cannot express that, so the equivalents live here and
-- question.answer_norm is only the canonical (ordinal 0) copy used for display and for the importer's
-- own checks. Grading reads this table, never answer_norm alone.
CREATE TABLE question_answer (
  question_id  INT UNSIGNED     NOT NULL,
  ordinal      TINYINT UNSIGNED NOT NULL,          -- 0 = canonical, 1.. = accepted equivalents
  text_raw     VARCHAR(64)      NOT NULL,          -- as written, for the parent's wrong-book page
  -- How to compare a submitted string with this one:
  --   exact   - normalised string equality only
  --   numeric - both sides parsed as a number/fraction/percent and compared exactly (sympy)
  --   text    - normalised equality, CJK punctuation folding, no numeric interpretation
  cmp_kind     ENUM('exact','numeric','text') NOT NULL DEFAULT 'exact',
  PRIMARY KEY (question_id, ordinal),
  CONSTRAINT fk_answer_question FOREIGN KEY (question_id) REFERENCES question (id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Multi-label questions (E4 keeps these at ~10%). is_primary marks the one in question.topic_id.
CREATE TABLE question_topic (
  question_id  INT UNSIGNED     NOT NULL,
  topic_id     INT UNSIGNED     NOT NULL,
  is_primary   TINYINT(1)       NOT NULL DEFAULT 0,
  PRIMARY KEY (question_id, topic_id),
  KEY idx_qt_topic (topic_id),
  CONSTRAINT fk_qt_question FOREIGN KEY (question_id) REFERENCES question (id) ON DELETE CASCADE,
  CONSTRAINT fk_qt_topic FOREIGN KEY (topic_id) REFERENCES topic (id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- runtime

CREATE TABLE device (
  id             CHAR(26)     NOT NULL,            -- ULID, also the `did` claim in a ticket
  name           VARCHAR(64)  NULL,                -- "客厅的小米"
  -- Pairing: the TV shows six characters, the parent types them into the H5. Only the hash is kept,
  -- and only until it is used (D4).
  pair_code_hash CHAR(64)     NULL,
  pair_expires_at DATETIME    NULL,
  bound_at       DATETIME     NULL,
  -- Hash of the bearer token the H5 keeps in a cookie. One parent per TV this release (D6): binding a
  -- new phone overwrites this and the old cookie stops working.
  token_hash     CHAR(64)     NULL,
  app_version    VARCHAR(32)  NULL,
  last_seen_at   DATETIME     NULL,
  revoked_at     DATETIME     NULL,
  created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_device_pair (pair_code_hash),
  KEY idx_device_token (token_hash)
) ENGINE=InnoDB;

-- Settings the parent changes in the H5 and the TV pulls by polling (D3, every 5 minutes plus on key
-- actions). Stored as one JSON blob so a new setting needs no migration.
CREATE TABLE device_config (
  device_id   CHAR(26)     NOT NULL,
  config_json JSON         NOT NULL,
  version     INT UNSIGNED NOT NULL DEFAULT 1,     -- bumped on write; the TV polls with the version it has
  updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (device_id),
  CONSTRAINT fk_config_device FOREIGN KEY (device_id) REFERENCES device (id)
) ENGINE=InnoDB;

-- The two counters that make the caps real (B4, B5). Enforced here, not on the TV: the TV also counts,
-- but only as a second line of defence.
CREATE TABLE daily_state (
  device_id      CHAR(26)     NOT NULL,
  day_key        DATE         NOT NULL,            -- the TV's local day; see the header note
  success_count  TINYINT UNSIGNED NOT NULL DEFAULT 0,  -- passed groups, cap 3
  awarded_ms     INT UNSIGNED NOT NULL DEFAULT 0,      -- earned time, cap 90 minutes
  attempt_count  TINYINT UNSIGNED NOT NULL DEFAULT 0,  -- started groups, cap 6
  fail_count     TINYINT UNSIGNED NOT NULL DEFAULT 0,  -- consecutive failures, drives the cooldown and C7
  cooldown_until DATETIME     NULL,
  -- What the TV reports it watched today, from the heartbeat. The server never counts viewing time
  -- itself (that happens on the TV, docs/PARENTAL.md 计时); this is only so the weekly report can draw
  -- a trend without asking the TV for seven days of history it does not keep.
  used_ms        INT UNSIGNED NOT NULL DEFAULT 0,
  -- When it last said so. NULL means "never reported", which is not the same as zero minutes: the
  -- parent page draws a dash rather than a confident 0.
  used_at        DATETIME     NULL,
  updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (device_id, day_key),
  CONSTRAINT fk_daily_device FOREIGN KEY (device_id) REFERENCES device (id)
) ENGINE=InnoDB;

CREATE TABLE quiz_session (
  id            CHAR(26)     NOT NULL,             -- ULID, handed to the TV
  device_id     CHAR(26)     NOT NULL,
  day_key       DATE         NOT NULL,
  issued_at     DATETIME     NOT NULL,
  expires_at    DATETIME     NOT NULL,             -- issued_at + 10 minutes (C4)
  status        ENUM('issued','submitted','expired','void') NOT NULL DEFAULT 'issued',
  -- Difficulty this group was drawn at. C7 drops it by one after three consecutive failures without
  -- telling the child, so the level actually served has to be recorded: without it there is no way to
  -- tell later whether a fail came from a harder group.
  target_difficulty TINYINT UNSIGNED NOT NULL DEFAULT 2,
  question_ids  JSON         NOT NULL,             -- the exact order served, so a resubmit cannot reorder
  score         TINYINT UNSIGNED NULL,             -- correct answers, 0..5
  passed        TINYINT(1)   NULL,
  award_ms      INT UNSIGNED NULL,                 -- 30 minutes on a pass; 0 otherwise. No half award (B2).
  ticket_jti    CHAR(26)     NULL,
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_session_device_day (device_id, day_key),
  CONSTRAINT fk_session_device FOREIGN KEY (device_id) REFERENCES device (id)
) ENGINE=InnoDB;

CREATE TABLE quiz_answer (
  session_id     CHAR(26)     NOT NULL,
  question_id    INT UNSIGNED NOT NULL,
  -- Denormalised from quiz_session so "which questions has this TV seen lately" is one index range
  -- scan instead of a join. The picker runs it on every serve, so it has to be cheap.
  device_id      CHAR(26)     NOT NULL,
  answered_day   DATE         NULL,                -- the TV's day_key, same basis as daily_state
  chosen         VARCHAR(64)  NULL,                -- option label for a choice, normalised text for a fill
  is_correct     TINYINT(1)   NOT NULL DEFAULT 0,  -- decided here and nowhere else
  flagged_unknown TINYINT(1)  NOT NULL DEFAULT 0,  -- "没学过" (B7); still answered
  answered_at    DATETIME     NULL,
  PRIMARY KEY (session_id, question_id),
  KEY idx_answer_recent (device_id, answered_day, question_id),
  KEY idx_answer_question (question_id, session_id),
  -- Feeds "do not repeat a question this device has seen": this index is the de-duplication.
  KEY idx_answer_question (question_id, session_id),
  CONSTRAINT fk_answer_session FOREIGN KEY (session_id) REFERENCES quiz_session (id) ON DELETE CASCADE,
  CONSTRAINT fk_answer_question FOREIGN KEY (question_id) REFERENCES question (id)
) ENGINE=InnoDB;

-- One row per awarded group. Kept after redemption: it is the audit trail behind a parent asking
-- "how did he get ninety minutes on Tuesday".
CREATE TABLE ticket (
  jti         CHAR(26)     NOT NULL,
  device_id   CHAR(26)     NOT NULL,
  session_id  CHAR(26)     NOT NULL,
  day_key     DATE         NOT NULL,               -- redeemable on this day only
  award_ms    INT UNSIGNED NOT NULL,
  issued_at   DATETIME     NOT NULL,
  expires_at  DATETIME     NOT NULL,               -- 24h, but day_key bites first (D1)
  redeemed_at DATETIME     NULL,
  PRIMARY KEY (jti),
  KEY idx_ticket_device_day (device_id, day_key),
  CONSTRAINT fk_ticket_device FOREIGN KEY (device_id) REFERENCES device (id),
  CONSTRAINT fk_ticket_session FOREIGN KEY (session_id) REFERENCES quiz_session (id)
) ENGINE=InnoDB;

-- Where the child is in the book (C3). Auto-advanced from the school calendar; the parent overrides by
-- moving current_unit_ordinal, never by editing the calendar.
CREATE TABLE curriculum_progress (
  device_id           CHAR(26)     NOT NULL,
  textbook_id         SMALLINT UNSIGNED NOT NULL,
  current_unit_ordinal TINYINT UNSIGNED NOT NULL DEFAULT 1,
  updated_by          ENUM('calendar','parent','child') NOT NULL DEFAULT 'calendar',
  updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (device_id, textbook_id),
  CONSTRAINT fk_progress_device FOREIGN KEY (device_id) REFERENCES device (id),
  CONSTRAINT fk_progress_textbook FOREIGN KEY (textbook_id) REFERENCES textbook (id)
) ENGINE=InnoDB;

-- Per-topic outcome for one device: the wrong-answer weighting (C6), the "没学过" escape count (B7),
-- and the three worst topics in the weekly report (C14).
CREATE TABLE topic_signal (
  device_id        CHAR(26)     NOT NULL,
  topic_id         INT UNSIGNED NOT NULL,
  right_count      SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  wrong_count      SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  unknown_count    SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  unknown_streak   TINYINT UNSIGNED  NOT NULL DEFAULT 0,  -- 3 in a row asks the parent to check (B7)
  last_seen_at     DATETIME     NULL,
  PRIMARY KEY (device_id, topic_id),
  KEY idx_signal_wrong (device_id, wrong_count),
  CONSTRAINT fk_signal_device FOREIGN KEY (device_id) REFERENCES device (id),
  CONSTRAINT fk_signal_topic FOREIGN KEY (topic_id) REFERENCES topic (id)
) ENGINE=InnoDB;

-- Every write a parent makes through the H5, and every settings change the TV pushes back. The spec
-- asks for it (docs/PARENTAL.md, 家长 H5: 所有改动写操作留痕) and it is also the only way to answer
-- "who set 30 minutes?" when the two ends disagree. `fields` lists the keys that changed, so a reader
-- does not have to diff two blobs to see what moved.
CREATE TABLE config_audit (
  id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  device_id  CHAR(26)     NOT NULL,
  source     ENUM('h5','tv') NOT NULL,
  account_id CHAR(26)     NULL,           -- who did it; NULL for the TV's own pushes and old rows
  fields     JSON         NOT NULL,
  old_json   JSON         NULL,
  new_json   JSON         NOT NULL,
  created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_audit_device (device_id, created_at),
  CONSTRAINT fk_audit_device FOREIGN KEY (device_id) REFERENCES device (id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- accounts (docs/ACCOUNT.md)
-- The phone's credential is an account now, not a device token. Email is a login name and nothing
-- more: there is no mail gateway, so nothing proves the address belongs to whoever typed it.
CREATE TABLE account (
  id            CHAR(26)     NOT NULL,
  email         VARCHAR(160) NOT NULL,
  email_key     VARCHAR(160) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,         -- scrypt$N$r$p$<salt>$<hash>
  fail_count    SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  locked_until  DATETIME     NULL,
  recovery_question    VARCHAR(200) NULL,      -- asked back when the password is lost
  recovery_answer_hash VARCHAR(255) NULL,      -- same scrypt, never kept in clear
  recover_fail_count   SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  recover_locked_until DATETIME     NULL,
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at DATETIME     NULL,
  disabled_at   DATETIME     NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_account_email (email_key)
) ENGINE=InnoDB;

-- One row per signed-in browser; only the hash of the cookie value is kept.
CREATE TABLE account_session (
  token_hash   CHAR(64)  NOT NULL,
  account_id   CHAR(26)  NOT NULL,
  created_at   DATETIME  NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at DATETIME  NULL,
  expires_at   DATETIME  NOT NULL,
  PRIMARY KEY (token_hash),
  KEY idx_session_account (account_id, expires_at),
  CONSTRAINT fk_session_account FOREIGN KEY (account_id) REFERENCES account (id)
) ENGINE=InnoDB;

-- Who may look at which television: two parents one TV, one parent two TVs.
CREATE TABLE account_device (
  account_id CHAR(26) NOT NULL,
  device_id  CHAR(26) NOT NULL,
  bound_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (account_id, device_id),
  KEY idx_device_accounts (device_id),
  CONSTRAINT fk_ad_account FOREIGN KEY (account_id) REFERENCES account (id),
  CONSTRAINT fk_ad_device  FOREIGN KEY (device_id) REFERENCES device (id)
) ENGINE=InnoDB;
