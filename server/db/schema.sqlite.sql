-- SQLite mirror of schema.sql, for local acceptance runs only. Production is MySQL 8.
--
-- Why a second file at all: the TV-side acceptance suite has to run on the developer machine, and this
-- machine has no MySQL. Keeping the port to one file beats inventing an ORM.
--
-- RULES
--   * Table and column names must match schema.sql exactly. `python3 -m server.tools.check_schema_parity`
--     compares the two files and fails on any drift, so a column added on one side cannot silently rot.
--   * Types are the closest SQLite equivalent: ENUM -> TEXT + CHECK, UNSIGNED -> INTEGER, JSON -> TEXT,
--     DATE/DATETIME -> TEXT (ISO-8601). The application never relies on a type that only MySQL has.
--   * ON UPDATE CURRENT_TIMESTAMP does not exist here; the application writes updated_at itself, which
--     it has to do anyway to stay portable.
--   * Foreign keys are declared but only enforced when `PRAGMA foreign_keys = ON` (server/app/db.py does).

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- content

CREATE TABLE subject (
  id          INTEGER NOT NULL,
  code        TEXT    NOT NULL,
  name_zh     TEXT    NOT NULL,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE (code)
);

CREATE TABLE textbook (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  subject_id  INTEGER NOT NULL,
  publisher   TEXT    NOT NULL,
  grade       INTEGER NOT NULL,
  term        TEXT    NOT NULL CHECK (term IN ('up','down')),
  title_zh    TEXT    NOT NULL,
  is_active   INTEGER NOT NULL DEFAULT 1,
  UNIQUE (subject_id, publisher, grade, term),
  FOREIGN KEY (subject_id) REFERENCES subject (id)
);

CREATE TABLE unit (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  textbook_id  INTEGER NOT NULL,
  ordinal      INTEGER NOT NULL,
  name_zh      TEXT    NOT NULL,
  week_start   INTEGER NULL,
  UNIQUE (textbook_id, ordinal),
  FOREIGN KEY (textbook_id) REFERENCES textbook (id)
);

CREATE TABLE topic (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  unit_id      INTEGER NOT NULL,
  ordinal      INTEGER NOT NULL,
  name_zh      TEXT    NOT NULL,
  summary_zh   TEXT    NULL,
  UNIQUE (unit_id, ordinal),
  FOREIGN KEY (unit_id) REFERENCES unit (id)
);

CREATE TABLE question (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  textbook_id      INTEGER NOT NULL,
  unit_id          INTEGER NOT NULL,
  topic_id         INTEGER NOT NULL,
  angle            TEXT    NOT NULL CHECK (angle IN ('recall','understand','apply')),
  difficulty       INTEGER NOT NULL,
  qtype            TEXT    NOT NULL CHECK (qtype IN ('choice','fill')),
  stem             TEXT    NOT NULL,
  stem_len         INTEGER NOT NULL,
  answer_norm      TEXT    NULL,
  explanation_zh   TEXT    NULL,
  verify_status    TEXT    NOT NULL DEFAULT 'draft'
                     CHECK (verify_status IN ('draft','sympy_ok','manual_ok','rejected')),
  verify_note      TEXT    NULL,
  distractor_score INTEGER NULL,
  source           TEXT    NOT NULL DEFAULT 'ai' CHECK (source IN ('ai','manual')),
  template_key     TEXT    NOT NULL DEFAULT 'manual',
  param_hash       TEXT    NOT NULL DEFAULT '',
  created_at       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  retired_at       TEXT    NULL,
  UNIQUE (topic_id, template_key, param_hash),
  FOREIGN KEY (textbook_id) REFERENCES textbook (id),
  FOREIGN KEY (topic_id) REFERENCES topic (id)
);
CREATE INDEX idx_question_pick  ON question (unit_id, difficulty, angle, id);
CREATE INDEX idx_question_topic ON question (topic_id, difficulty);

CREATE TABLE question_option (
  question_id  INTEGER NOT NULL,
  label        TEXT    NOT NULL,
  text         TEXT    NOT NULL,
  is_correct   INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (question_id, label),
  FOREIGN KEY (question_id) REFERENCES question (id) ON DELETE CASCADE
);

CREATE TABLE question_answer (
  question_id  INTEGER NOT NULL,
  ordinal      INTEGER NOT NULL,
  text_raw     TEXT    NOT NULL,
  cmp_kind     TEXT    NOT NULL DEFAULT 'exact' CHECK (cmp_kind IN ('exact','numeric','text')),
  PRIMARY KEY (question_id, ordinal),
  FOREIGN KEY (question_id) REFERENCES question (id) ON DELETE CASCADE
);

CREATE TABLE question_topic (
  question_id  INTEGER NOT NULL,
  topic_id     INTEGER NOT NULL,
  is_primary   INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (question_id, topic_id),
  FOREIGN KEY (question_id) REFERENCES question (id) ON DELETE CASCADE,
  FOREIGN KEY (topic_id) REFERENCES topic (id)
);
CREATE INDEX idx_qt_topic ON question_topic (topic_id);

-- ---------------------------------------------------------------- runtime

CREATE TABLE device (
  id              TEXT NOT NULL,
  name            TEXT NULL,
  pair_code_hash  TEXT NULL,
  pair_expires_at TEXT NULL,
  bound_at        TEXT NULL,
  token_hash      TEXT NULL,
  app_version     TEXT NULL,
  last_seen_at    TEXT NULL,
  revoked_at      TEXT NULL,
  created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE (pair_code_hash)
);
CREATE INDEX idx_device_token ON device (token_hash);

CREATE TABLE device_config (
  device_id   TEXT    NOT NULL,
  config_json TEXT    NOT NULL,
  version     INTEGER NOT NULL DEFAULT 1,
  updated_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (device_id),
  FOREIGN KEY (device_id) REFERENCES device (id)
);

CREATE TABLE daily_state (
  device_id      TEXT    NOT NULL,
  day_key        TEXT    NOT NULL,
  success_count  INTEGER NOT NULL DEFAULT 0,
  awarded_ms     INTEGER NOT NULL DEFAULT 0,
  attempt_count  INTEGER NOT NULL DEFAULT 0,
  fail_count     INTEGER NOT NULL DEFAULT 0,
  cooldown_until TEXT    NULL,
  used_ms        INTEGER NOT NULL DEFAULT 0,    -- what the TV says it watched today (heartbeat)
  used_at        TEXT    NULL,                  -- when it last said so; NULL means never, not zero
  updated_at     TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (device_id, day_key),
  FOREIGN KEY (device_id) REFERENCES device (id)
);

CREATE TABLE quiz_session (
  id                TEXT    NOT NULL,
  device_id         TEXT    NOT NULL,
  day_key           TEXT    NOT NULL,
  issued_at         TEXT    NOT NULL,
  expires_at        TEXT    NOT NULL,
  status            TEXT    NOT NULL DEFAULT 'issued'
                      CHECK (status IN ('issued','submitted','expired','void')),
  target_difficulty INTEGER NOT NULL DEFAULT 2,
  question_ids      TEXT    NOT NULL,
  score             INTEGER NULL,
  passed            INTEGER NULL,
  award_ms          INTEGER NULL,
  ticket_jti        TEXT    NULL,
  created_at        TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  FOREIGN KEY (device_id) REFERENCES device (id)
);
CREATE INDEX idx_session_device_day ON quiz_session (device_id, day_key);

CREATE TABLE quiz_answer (
  session_id      TEXT    NOT NULL,
  question_id     INTEGER NOT NULL,
  device_id       TEXT    NOT NULL,
  answered_day    TEXT    NULL,
  chosen          TEXT    NULL,
  is_correct      INTEGER NOT NULL DEFAULT 0,
  flagged_unknown INTEGER NOT NULL DEFAULT 0,
  answered_at     TEXT    NULL,
  PRIMARY KEY (session_id, question_id),
  FOREIGN KEY (session_id) REFERENCES quiz_session (id) ON DELETE CASCADE,
  FOREIGN KEY (question_id) REFERENCES question (id)
);
CREATE INDEX idx_answer_recent   ON quiz_answer (device_id, answered_day, question_id);
CREATE INDEX idx_answer_question ON quiz_answer (question_id, session_id);

CREATE TABLE ticket (
  jti         TEXT    NOT NULL,
  device_id   TEXT    NOT NULL,
  session_id  TEXT    NOT NULL,
  day_key     TEXT    NOT NULL,
  award_ms    INTEGER NOT NULL,
  issued_at   TEXT    NOT NULL,
  expires_at  TEXT    NOT NULL,
  redeemed_at TEXT    NULL,
  PRIMARY KEY (jti),
  FOREIGN KEY (device_id) REFERENCES device (id),
  FOREIGN KEY (session_id) REFERENCES quiz_session (id)
);
CREATE INDEX idx_ticket_device_day ON ticket (device_id, day_key);

CREATE TABLE curriculum_progress (
  device_id            TEXT    NOT NULL,
  textbook_id          INTEGER NOT NULL,
  current_unit_ordinal INTEGER NOT NULL DEFAULT 1,
  updated_by           TEXT    NOT NULL DEFAULT 'calendar'
                         CHECK (updated_by IN ('calendar','parent','child')),
  updated_at           TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (device_id, textbook_id),
  FOREIGN KEY (device_id) REFERENCES device (id),
  FOREIGN KEY (textbook_id) REFERENCES textbook (id)
);

CREATE TABLE topic_signal (
  device_id      TEXT    NOT NULL,
  topic_id       INTEGER NOT NULL,
  right_count    INTEGER NOT NULL DEFAULT 0,
  wrong_count    INTEGER NOT NULL DEFAULT 0,
  unknown_count  INTEGER NOT NULL DEFAULT 0,
  unknown_streak INTEGER NOT NULL DEFAULT 0,
  last_seen_at   TEXT    NULL,
  PRIMARY KEY (device_id, topic_id),
  FOREIGN KEY (device_id) REFERENCES device (id),
  FOREIGN KEY (topic_id) REFERENCES topic (id)
);
CREATE INDEX idx_signal_wrong ON topic_signal (device_id, wrong_count);

-- Every write a parent makes through the H5, and every settings change the TV pushes back. The spec
-- asks for it (docs/PARENTAL.md, 家长 H5: 所有改动写操作留痕) and it is also the only way to answer
-- "who set 30 minutes?" when the two ends disagree. `fields` is a JSON array of the keys that changed,
-- so a reader does not have to diff two blobs to see what moved.
CREATE TABLE config_audit (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id  TEXT    NOT NULL,
  source     TEXT    NOT NULL CHECK (source IN ('h5','tv')),
  -- Which account did it, once there are accounts. Null means "the TV itself" or a write from
  -- before this column existed; `source` says which side, this says who.
  account_id TEXT    NULL,
  fields     TEXT    NOT NULL,
  old_json   TEXT    NULL,
  new_json   TEXT    NOT NULL,
  created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (device_id) REFERENCES device (id)
);
CREATE INDEX idx_audit_device ON config_audit (device_id, created_at);

-- ---------------------------------------------------------------- accounts (docs/ACCOUNT.md)
-- The phone's credential is an account now, not a device token. Email is a login name and nothing
-- more: there is no mail gateway, so nothing proves the address belongs to whoever typed it. What it
-- does buy is an identity the audit table and the bindings can point at.
CREATE TABLE account (
  id            TEXT NOT NULL,                 -- ULID, like every other id here
  email         TEXT NOT NULL,                 -- as typed, for display
  email_key     TEXT NOT NULL,                 -- lower(trim(email)); the unique one
  password_hash TEXT NOT NULL,                 -- scrypt$N$r$p$<salt>$<hash>, self-describing
  fail_count    INTEGER NOT NULL DEFAULT 0,    -- consecutive wrong passwords, persisted so a
  locked_until  TEXT    NULL,                  -- restart does not hand out a fresh budget
  recovery_question    TEXT NULL,              -- asked back on the phone when the password is lost;
  recovery_answer_hash TEXT NULL,              -- hashed with the same scrypt, never kept in clear
  recover_fail_count   INTEGER NOT NULL DEFAULT 0,  -- wrong answers, a separate budget from the
  recover_locked_until TEXT    NULL,                -- password, so one does not spend the other
  created_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at TEXT    NULL,
  disabled_at   TEXT    NULL,
  PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uk_account_email ON account (email_key);

-- One row per signed-in browser. Only the hash of the cookie value is kept, the same rule the device
-- token followed. Expiry is sliding: used within 30 days and it is pushed out again, up to 90.
CREATE TABLE account_session (
  token_hash   TEXT NOT NULL,
  account_id   TEXT NOT NULL,
  created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TEXT NULL,
  expires_at   TEXT NOT NULL,
  PRIMARY KEY (token_hash),
  FOREIGN KEY (account_id) REFERENCES account (id)
);
CREATE INDEX idx_session_account ON account_session (account_id, expires_at);

-- Who may look at which television. Many-to-many on purpose (A2): two parents, one TV; one parent,
-- two TVs. `device.bound_at` is the derived "at least one account still holds this TV".
CREATE TABLE account_device (
  account_id TEXT NOT NULL,
  device_id  TEXT NOT NULL,
  bound_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (account_id, device_id),
  FOREIGN KEY (account_id) REFERENCES account (id),
  FOREIGN KEY (device_id) REFERENCES device (id)
);
CREATE INDEX idx_device_accounts ON account_device (device_id);
