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
  -- Stable business key of the aggregate node: 'math-6up-u01', 'math-6up-u-review'. The report prints
  -- this instead of assembling a string from subject code + ordinal, so an aggregate is addressable
  -- from outside the database and does not change when the book is re-cut.
  code         VARCHAR(24)       NULL,
  name_zh      VARCHAR(64)       NOT NULL,
  -- Which school week this unit is normally taught in. Drives the calendar-based auto-advance (C3-B);
  -- the parent only ever overrides it.
  week_start   TINYINT UNSIGNED  NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_unit (textbook_id, ordinal),
  UNIQUE KEY uk_unit_code (code),
  CONSTRAINT fk_unit_textbook FOREIGN KEY (textbook_id) REFERENCES textbook (id)
) ENGINE=InnoDB;

-- The atom everything joins on: wrong-answer stats, similar-question generation, progress matching.
CREATE TABLE topic (
  id           INT UNSIGNED      NOT NULL AUTO_INCREMENT,
  unit_id      SMALLINT UNSIGNED NOT NULL,
  ordinal      TINYINT UNSIGNED  NOT NULL,
  -- The chapter's position in the book, copied from unit.ordinal at import. The picker reads this to
  -- decide "current / already taught / not yet taught", so it must belong to the node rather than to a
  -- unit row that a question happens to point at (question.unit_id is now empty by design).
  chapter_order TINYINT UNSIGNED NOT NULL DEFAULT 0,
  -- Stable business key, 'math-6up-d01'. The question files and docs/data_sources.md name nodes by
  -- this, because the auto-increment id is not known until the tree is imported and must not leak into
  -- content a human maintains.
  code         VARCHAR(24)       NULL,
  name_zh      VARCHAR(64)       NOT NULL,        -- '分数乘整数'
  -- One line the parent reads in the weekly report; not shown to the child (C13 shows the name only).
  summary_zh   VARCHAR(255)      NULL,
  -- The textbook's own sub-points this node covers, in the taxonomy's own words. JSON array of
  -- {source_id, name, evidence, assessment_prompt}. JSON because nothing joins on it: the picker asks
  -- for topic_id and the report prints names. Empty for a subject with no public taxonomy (语文/英语).
  micro_nodes_json JSON          NULL,
  -- The taxonomy ids behind micro_nodes, same order. Kept because the file names its sources and an
  -- export that dropped them could not be checked against the snapshot any more.
  source_ids_json  JSON          NULL,
  -- Which other diagnostic nodes should be solid first, as taxonomy ids. Advisory: the picker does not
  -- reorder by it (a child should not be blocked from a topic the calendar is teaching today).
  prereq_json  JSON              NULL,
  -- What kind of knowledge this node is (docs/RULES.md §2.3a): procedural/conceptual/representational
  -- for maths, grammar/vocabulary/sentence_pattern/reading/listening_speaking for English, and the
  -- Chinese set 字词基础/阅读理解/语言运用/习作方法. Free text rather than an ENUM: the three subjects
  -- share one column and their vocabularies only overlap by accident.
  knowledge_type VARCHAR(16)     NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_topic (unit_id, ordinal),
  UNIQUE KEY uk_topic_code (code),
  CONSTRAINT fk_topic_unit FOREIGN KEY (unit_id) REFERENCES unit (id)
) ENGINE=InnoDB;

CREATE TABLE question (
  id               INT UNSIGNED      NOT NULL AUTO_INCREMENT,
  textbook_id      SMALLINT UNSIGNED NOT NULL,
  -- Nullable, and null for every hand-written question: a question hangs from a diagnostic node, never
  -- from a chapter. It used to be a NOT NULL pointer to `unit` that the picker joined for the ordinal;
  -- that made aggregate nodes physical parents of questions, which is exactly what the two-level model
  -- forbids. The ordinal now comes from topic.chapter_order.
  unit_id          SMALLINT UNSIGNED NULL,
  topic_id         INT UNSIGNED      NOT NULL,     -- the diagnostic node (E4: one label, ~10% multi)
  -- Stable business key from the question file ('math-6up-d01-q01'). The file is the source; the
  -- database id is an implementation detail and must not leak into an export or an answer record.
  code             VARCHAR(40)       NULL,
  -- Denormalised from the textbook so "every English question" is one index scan, and so a question
  -- carries its subject even if it is ever moved between books.
  subject_id       SMALLINT UNSIGNED NULL,
  angle            ENUM('recall','understand','apply') NOT NULL,
  -- What the child has to do with the knowledge, per subject (see docs/RULES.md §2.3a). Kept beside
  -- `angle` rather than replacing it: angle is what the picker sorts and indexes on, ability_tag is
  -- what the report splits a node by. NULL on rows imported before the split.
  ability_tag      VARCHAR(16)       NULL,
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
  -- Which pipeline produced it (docs/WRONGBOOK.md §8). Deliberately not folded into `source`: that
  -- column says who typed it (ai/manual) and already has data; this one says which assembly line ran.
  origin           ENUM('seed','template','ai_similar') NOT NULL DEFAULT 'seed',
  source_wrong_item CHAR(26)         NULL,         -- which mistake caused it; NULL for seeds
  -- A similar question belongs to one TV's weak spot, not to the shared bank. Without this one child's
  -- Chinese mistakes would start appearing in another child's groups as soon as there are two devices.
  owner_device     CHAR(26)          NULL,         -- NULL = public bank
  PRIMARY KEY (id),
  UNIQUE KEY uk_question_dedup (topic_id, template_key, param_hash),
  UNIQUE KEY uk_question_code (code),
  KEY idx_question_pick (topic_id, difficulty, angle, id),
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
  -- Hash of the 32-byte secret the television keeps in its own preferences. Every request the TV makes
  -- carries it as `Authorization: Bearer …`, and `device_id` alone stopped being enough on
  -- 2026-11-17: on a LAN an id was a name, on the internet it is half a password. NULL only on rows
  -- created before this column existed, which are refused until they re-register.
  secret_hash    CHAR(64)     NULL,
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
  -- The mix snapshot for today (docs/WRONGBOOK.md §6.6): written once with the day's first group and
  -- then read unchanged, so a bad answer at 17:00 cannot reshuffle the 20:00 group. All NULL on rows
  -- that predate this feature, which the picker reads as "no snapshot yet, take one now".
  mix_state      ENUM('S0','S1','S2','S3') NULL,
  mix_c          TINYINT UNSIGNED NULL,        -- current-unit slots
  mix_r          TINYINT UNSIGNED NULL,        -- review slots
  mix_n          TINYINT UNSIGNED NULL,        -- how many weak topics drove the choice
  -- How deep the weakest one was (3 = not mastered, 1 = basic). Frozen with the rest of the snapshot:
  -- level can recover during the day, and a later group must still be sized by the morning's picture.
  mix_deepest    TINYINT UNSIGNED NULL,
  mix_note       VARCHAR(120) NULL,            -- one line for the parent: why it went up or down
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
  -- Which of the served questions came from the review segment, as a JSON array of ids. The mix metric
  -- needs this after the fact: recomputing it from curriculum_progress is not reliable, because the
  -- calendar may have moved to the next unit while the child was still answering this group.
  -- Written for the server's own accounting; never sent to the TV (docs/WRONGBOOK.md §10).
  review_question_ids JSON   NULL,
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
  -- Denormalised from the question so the answer record is readable on its own: "which node was this,
  -- and what ability was it testing" is the first question a report asks, and joining three tables to
  -- answer it is how a wrong join survives unnoticed. NULL on rows written before 2026-11-19.
  diagnostic_node_id VARCHAR(24) NULL,             -- topic.code
  ability_tag    VARCHAR(16)  NULL,
  flagged_unknown TINYINT(1)  NOT NULL DEFAULT 0,  -- "没学过" (B7); still answered
  answered_at    DATETIME     NULL,
  PRIMARY KEY (session_id, question_id),
  KEY idx_answer_recent (device_id, answered_day, question_id),
  -- Feeds "do not repeat a question this device has seen": this index is the de-duplication.
  KEY idx_answer_question (question_id, session_id),
  CONSTRAINT fk_answer_session FOREIGN KEY (session_id) REFERENCES quiz_session (id) ON DELETE CASCADE,
  CONSTRAINT fk_quizanswer_question FOREIGN KEY (question_id) REFERENCES question (id)
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

-- When this term's first week started, so "which unit are we on" can be worked out from the calendar
-- instead of being a number nobody updates (docs/RULES.md §3.0). One row per device: only one term is
-- in progress at a time. The parent can override any unit by hand; that override wins over this date.
CREATE TABLE term_anchor (
  device_id   CHAR(26) NOT NULL,
  start_date  DATE     NOT NULL,
  updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (device_id),
  CONSTRAINT fk_anchor_device FOREIGN KEY (device_id) REFERENCES device (id)
) ENGINE=InnoDB;


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

-- ---------------------------------------------------------------- wrong book (docs/WRONGBOOK.md)
-- Nothing here is ever shown to the child. No topic name, no counter, no "review this" wording reaches
-- the TV (docs/WRONGBOOK.md §10): these tables feed the picker and the parent H5, and only those two.
-- Kept at the end of the file on purpose: paper.account_id points at account, and MySQL refuses a
-- foreign key whose target table has not been created yet. Moving this block up breaks the import.

-- A batch of photographed pages (D2: the OCR part is a separate project, so for now a paper is a
-- container the parent fills by hand — one row per upload, not per question).
CREATE TABLE paper (
  id             CHAR(26)         NOT NULL,          -- ULID
  device_id      CHAR(26)         NOT NULL,
  account_id     CHAR(26)         NOT NULL,           -- who uploaded it; the audit needs an answer
  subject_id     TINYINT UNSIGNED NULL,               -- optional: narrows the topic list the parent sees
  shot_count     TINYINT UNSIGNED NOT NULL DEFAULT 0,
  -- 'none' is a normal resting state until OCR lands: a hand-entered paper never had text recognised.
  ocr_status     ENUM('none','queued','done','failed') NOT NULL DEFAULT 'none',
  item_total     SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  item_confirmed SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  captured_at    DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
  confirmed_at   DATETIME         NULL,
  purge_at       DATETIME         NOT NULL,           -- captured_at + 30 days (D4)
  purged_at      DATETIME         NULL,               -- NULL until the photos are actually gone
  PRIMARY KEY (id),
  KEY idx_paper_device (device_id, captured_at),
  KEY idx_paper_purge (purge_at),
  CONSTRAINT fk_paper_device  FOREIGN KEY (device_id)  REFERENCES device (id),
  CONSTRAINT fk_paper_account FOREIGN KEY (account_id) REFERENCES account (id),
  CONSTRAINT fk_paper_subject FOREIGN KEY (subject_id) REFERENCES subject (id)
) ENGINE=InnoDB;

-- One row per mistake, from either source. This is the record a parent scrolls, and the thing the
-- mastery ledger points back at. A TV mistake is written in the same transaction as the grade; a paper
-- mistake waits for the parent to confirm, because a wrong guess here poisons the next group.
CREATE TABLE wrong_item (
  id          CHAR(26)         NOT NULL,
  device_id   CHAR(26)         NOT NULL,
  source      ENUM('tv','paper') NOT NULL,
  -- Exactly one of these two is set: a TV mistake names the question that was served, a paper mistake
  -- names the batch it came from.
  question_id INT UNSIGNED     NULL,
  paper_id    CHAR(26)         NULL,
  topic_id    INT UNSIGNED     NOT NULL,              -- what the weighting actually keys on
  stem_text   TEXT             NULL,                  -- recognised or typed; never served back as a question
  occurred_on DATE             NOT NULL,              -- the TV's day_key basis (04:00 rollover)
  -- 'confirmed' for TV rows (the grade is the evidence); 'pending' for paper rows until a parent agrees.
  status      ENUM('pending','confirmed','rejected','purged') NOT NULL DEFAULT 'confirmed',
  confidence  ENUM('high','low') NULL,                -- only meaningful for paper rows
  created_at  DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_wrong_item_topic (device_id, topic_id, occurred_on),
  KEY idx_wrong_item_paper (paper_id),
  KEY idx_wrong_item_question (question_id),
  CONSTRAINT fk_wrong_device   FOREIGN KEY (device_id)   REFERENCES device (id),
  CONSTRAINT fk_wrong_topic    FOREIGN KEY (topic_id)    REFERENCES topic (id),
  CONSTRAINT fk_wrong_question FOREIGN KEY (question_id) REFERENCES question (id),
  CONSTRAINT fk_wrong_paper    FOREIGN KEY (paper_id)    REFERENCES paper (id)
) ENGINE=InnoDB;

-- One row per device × topic: the number the parent reads and the weight the picker uses. `level` and
-- `weight_x10` are stored rather than derived because both are read on every serve. The truth is the
-- rolling window below (`acc_x1000` and friends), recomputed from mastery_event after every answer;
-- `score` is that window as 0..100 where higher means better, kept because the ledger's replay
-- contract and every screen are written against it.
CREATE TABLE topic_mastery (
  device_id     CHAR(26)         NOT NULL,
  topic_id      INT UNSIGNED     NOT NULL,
  score         TINYINT UNSIGNED NOT NULL DEFAULT 0,  -- 0..100 mastery; higher is better
  level         TINYINT UNSIGNED NOT NULL DEFAULT 0,  -- L0 not mastered .. L3 extended
  weight_x10    SMALLINT UNSIGNED NOT NULL DEFAULT 10, -- 10/20/30/40: integers, so no float rounding
  target_qty    TINYINT UNSIGNED NOT NULL DEFAULT 0,  -- 0/5/7/10; the generator fills up to this
  ready_qty     SMALLINT UNSIGNED NOT NULL DEFAULT 0, -- cache of "servable questions right now"
  -- The rolling window: the last MASTERY_WINDOW (10) answers on this topic, weighted by difficulty
  -- (easy 10, medium 15, hard 20, all x10). acc_x1000 = correct_weight / weight_sum. Recomputed after
  -- every write and cached here because the parent page reads one row per topic.
  sample_count       TINYINT UNSIGNED  NOT NULL DEFAULT 0, -- 0..10; under 3 the node reads 待测
  weight_sum_x10     SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  correct_weight_x10 SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  acc_x1000          SMALLINT UNSIGNED NOT NULL DEFAULT 0, -- 0..1000
  hard_right         TINYINT UNSIGNED  NOT NULL DEFAULT 0, -- hard questions answered right in the window
  -- Monotonic per topic. mastery_event orders its window by this, not by created_at: one submit writes
  -- five rows sharing a timestamp, and ULIDs are not ordered within a millisecond.
  seq           INT UNSIGNED      NOT NULL DEFAULT 0,
  -- The parent's manual correction, cumulative and clamped to ±30. Applied on top of the window rather
  -- than written into it, so one tap cannot fake evidence and it survives the window rolling over.
  bias          SMALLINT          NOT NULL DEFAULT 0,
  -- Five wrong in a row takes the topic off the review segment for three days. `wrong_streak` is
  -- stored rather than derived from the ledger because it is read on every grade.
  -- `right_streak` has no reader since the weighted model; kept so existing rows stay meaningful.
  right_streak  TINYINT UNSIGNED NOT NULL DEFAULT 0,
  wrong_streak  TINYINT UNSIGNED NOT NULL DEFAULT 0,
  paused_until  DATETIME         NULL,                -- five wrong in a row: off the review segment
  total_wrong   INT UNSIGNED     NOT NULL DEFAULT 0,
  total_right   INT UNSIGNED     NOT NULL DEFAULT 0,
  -- When it became weak (level crossed into 0 or 1); NULL while it is not weak. The day's "newcomer"
  -- slot keys on this, never on last_event_at: a right answer refreshes that one too.
  weak_since    DATETIME         NULL,
  -- The per-ability split of the same window, persisted rather than recomputed when a report is opened:
  -- [{"tag":"执行计算","acc":750,"sample":4}, ...]. A drill-down that has to be recomputed at read time
  -- is a drill-down that drifts from the level it is explaining.
  ability_breakdown_json JSON    NULL,
  last_event_at DATETIME         NULL,
  updated_at    DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (device_id, topic_id),
  KEY idx_mastery_weak (device_id, level),
  CONSTRAINT fk_mastery_device FOREIGN KEY (device_id) REFERENCES device (id),
  CONSTRAINT fk_mastery_topic  FOREIGN KEY (topic_id)  REFERENCES topic (id)
) ENGINE=InnoDB;

-- Mastery per (device, topic, ability line). A second table rather than a second key on topic_mastery,
-- because the two answer different questions and are read by different callers: the weak list, the
-- daily mix quota and the picker's review segment all ask "how is this node" (topic_mastery), while a
-- drill-down and a targeted re-ask ask "how is this line of it" (here).
--
-- The window is per cell, not a slice of the node's window. That is the whole reason this table exists:
-- with one ten-row window per node, four ability lines cannot each hold MIN_SAMPLE (3) rows — twelve
-- rows do not fit in ten, and a rolling window forgets the first ones. Each cell therefore keeps its
-- own last ten `counted=1` events, read from mastery_event by (device, topic, ability).
--
-- A paper mistake has no ability line (no question behind it), so it only ever moves the node row.
CREATE TABLE ability_mastery (
  device_id     CHAR(26)          NOT NULL,
  topic_id      INT UNSIGNED      NOT NULL,
  ability_tag   VARCHAR(16)       NOT NULL,   -- subject-specific; see docs/RULES.md and question.ability_tag
  score         TINYINT UNSIGNED  NOT NULL DEFAULT 0,   -- 0..100, for the drill-down's own bar
  level         TINYINT UNSIGNED  NOT NULL DEFAULT 0,   -- 0 未掌握 .. 3 拓展, judged on this cell's window
  sample_count       TINYINT UNSIGNED  NOT NULL DEFAULT 0, -- 0..10; under 3 the cell reads 待测
  weight_sum_x10     SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  correct_weight_x10 SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  acc_x1000          SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  hard_right         TINYINT UNSIGNED  NOT NULL DEFAULT 0,
  -- Per-cell sequence, so "replay this cell's window" does not have to filter the node's ledger by hand.
  seq           INT UNSIGNED      NOT NULL DEFAULT 0,
  weak_since    DATETIME          NULL,
  updated_at    DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (device_id, topic_id, ability_tag),
  KEY idx_ability_weak (device_id, level),
  CONSTRAINT fk_ability_mastery_device FOREIGN KEY (device_id) REFERENCES device (id),
  CONSTRAINT fk_ability_mastery_topic  FOREIGN KEY (topic_id)  REFERENCES topic (id)
) ENGINE=InnoDB;

-- The weak list, materialised. Before this table the "what to teach this week" page was reassembled
-- from topic_mastery on every read, so a weakness had no identity, no history and no status: it was a
-- row that happened to satisfy `level <= 1 AND sample >= 3`. One row per (device, topic), written in the
-- same transaction as the mastery update that caused it, so the list and the score can never disagree.
--
-- `resolved` is a flag rather than a delete: a parent asking "was he weak in 分数除法 last month" is a
-- question the row can still answer, and deleting it would answer "no such node". A resolved row is
-- skipped by the report; a later relapse clears the flag on the same row (`opened_at` keeps the first).
CREATE TABLE weakness (
  weakness_id    CHAR(26)      NOT NULL,
  device_id      CHAR(26)      NOT NULL,             -- the student axis (one TV is one child)
  topic_id       INT UNSIGNED  NOT NULL,
  diagnostic_node_id VARCHAR(24) NOT NULL,           -- topic.code, so the row is readable without a join
  mastery_level  TINYINT UNSIGNED NOT NULL,          -- 0 or 1; the report prints 0-未掌握 / 1-基础
  -- ×1000, the same scale as topic_mastery.acc_x1000. An integer because the model is integer: two
  -- representations of one number is how a report ends up disagreeing with its own drill-down.
  weighted_accuracy SMALLINT UNSIGNED NOT NULL,
  sample_count   TINYINT UNSIGNED NOT NULL,
  evidence_question_ids JSON   NULL,                 -- three question codes, newest first
  parent_aggregate_node_id VARCHAR(24) NOT NULL,     -- unit.code, e.g. math-6up-u01
  parent_aggregate_name    VARCHAR(64) NOT NULL,
  resolved       TINYINT(1)    NOT NULL DEFAULT 0,      -- 0 = active, 1 = resolved (docs/weakness-calc-mapping.md)
  opened_at      DATETIME      NOT NULL,                -- first detection; a relapse keeps it
  -- The state machine's frozen explanations. Written once at detection and never rewritten by a later
  -- window, so "why was this called weak" stays answerable after the child has recovered.
  initial_level  TINYINT UNSIGNED NULL,                 -- band at detection (0 or 1)
  detection_snapshot     JSON NULL,                     -- wa / n / difficulty mix at detection
  detection_history      JSON NULL,                     -- previous detection snapshots, oldest first
  current_window_snapshot JSON NULL,                    -- wa / n / medium_correct / hard_correct now
  closing_snapshot       JSON NULL,                     -- the four numbers at the moment it closed
  cause_tags     JSON NULL,                             -- up to two ability lines that explain it
  cause_status   VARCHAR(24) NULL,                      -- ok / insufficient_sample
  resolved_at    DATETIME NULL,
  reopen_count   TINYINT UNSIGNED NOT NULL DEFAULT 0,
  reopened_at    DATETIME NULL,
  -- Moves only when a new answer enters the window: a parent's ±10 must not look like practice.
  last_practiced_at DATETIME NULL,
  updated_at     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (weakness_id),
  UNIQUE KEY uk_weakness (device_id, topic_id),
  KEY idx_weakness_open (device_id, resolved, mastery_level),
  CONSTRAINT fk_weakness_device FOREIGN KEY (device_id) REFERENCES device (id),
  CONSTRAINT fk_weakness_topic  FOREIGN KEY (topic_id)  REFERENCES topic (id)
) ENGINE=InnoDB;

-- The ledger. Every change to topic_mastery.score has a row here, so "why is this at 72" is a SELECT
-- rather than an argument. `score_after` is the redundancy that makes the ledger checkable: replay the
-- deltas and the last one must equal the stored score. Rows with counted=1 are also the window the
-- weighted accuracy is computed from, which is why the evidence columns live here instead of a second
-- table: an answer is one fact, not two.
CREATE TABLE mastery_event (
  id          CHAR(26)       NOT NULL,
  device_id   CHAR(26)       NOT NULL,
  topic_id    INT UNSIGNED   NOT NULL,
  -- The question this row is evidence for. Kept so a report can name the three answers behind a number
  -- without parsing `ref_id` ('<session>:<question>') back into parts.
  question_id INT UNSIGNED   NULL,
  -- Copied from the question, not joined at read time: the per-cell window is
  -- ORDER BY seq DESC LIMIT 10 filtered by this column, and a join on every answer would make the
  -- hottest write path depend on `question`. NULL for a paper mistake, which has no line.
  ability_tag VARCHAR(16)    NULL,
  kind        ENUM('tv_wrong','tv_right','paper_wrong','streak_bonus','parent_adjust',
                   'bad_question_void') NOT NULL,
  delta       SMALLINT       NOT NULL,               -- score_after - score_before, signed
  score_after TINYINT UNSIGNED NOT NULL,
  -- The evidence behind a counted row. NULL on rows that are bookkeeping only (parent_adjust,
  -- bad_question_void, model_rebase-era rows).
  is_correct  TINYINT(1)         NULL,
  difficulty  TINYINT UNSIGNED   NULL,               -- 1/2/3; NULL for a paper mistake
  weight_x10  SMALLINT UNSIGNED  NULL,               -- 10/15/20 as counted
  -- 1 = inside the rolling window, 0 = ledger only. Defaults to 0 so a writer that forgets the flag
  -- cannot silently pollute the accuracy of a topic.
  counted     TINYINT(1)     NOT NULL DEFAULT 0,
  -- The window's state after this row, so a parent can read "答对一道中档题，正确率 60%" without
  -- replaying ten rows. Mirrors topic_mastery.acc_x1000 / sample_count.
  acc_after   SMALLINT UNSIGNED  NULL,
  sample_after TINYINT UNSIGNED  NULL,
  -- Position in this topic's own sequence; the window is ORDER BY seq DESC LIMIT 10.
  seq         INT UNSIGNED   NOT NULL DEFAULT 0,
  -- What caused it: wrong_item.id for a paper mistake, '<session_id>:<question_id>' for a TV answer.
  -- VARCHAR rather than CHAR(26) because the TV case carries both halves of quiz_answer's key.
  ref_id      VARCHAR(64)    NULL,
  note        VARCHAR(200)   NULL,
  created_at  DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_mastery_event_topic (device_id, topic_id, created_at),
  KEY idx_event_window (device_id, topic_id, seq),
  -- The per-cell window: same shape as idx_event_window plus the line, so a cell read is one range scan.
  KEY idx_event_ability (device_id, topic_id, ability_tag, seq),
  CONSTRAINT fk_event_device FOREIGN KEY (device_id) REFERENCES device (id),
  CONSTRAINT fk_event_topic  FOREIGN KEY (topic_id)  REFERENCES topic (id)
) ENGINE=InnoDB;

-- Generation is asynchronous: the parent confirming a paper should not wait for templates to run. One
-- row per request, `need_count` up front and `done_count` as it goes, so a crash mid-run is resumable.
CREATE TABLE gen_task (
  id          CHAR(26)         NOT NULL,
  device_id   CHAR(26)         NOT NULL,
  topic_id    INT UNSIGNED     NOT NULL,
  level       TINYINT UNSIGNED NOT NULL,              -- the level at request time, for the audit
  need_count  SMALLINT UNSIGNED NOT NULL,
  done_count  SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  -- One route on purpose (D3). A model-generated route would be a new value here plus a new review
  -- step; until then the CHECK keeps a second path from sneaking in unannounced.
  route       ENUM('template') NOT NULL DEFAULT 'template',
  status      ENUM('queued','running','done','failed','skipped') NOT NULL DEFAULT 'queued',
  attempts    TINYINT UNSIGNED NOT NULL DEFAULT 0,
  last_error  VARCHAR(500)     NULL,
  created_at  DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_gen_queued (status, created_at),
  KEY idx_gen_topic (device_id, topic_id, status),
  CONSTRAINT fk_gen_device FOREIGN KEY (device_id) REFERENCES device (id),
  CONSTRAINT fk_gen_topic  FOREIGN KEY (topic_id)  REFERENCES topic (id)
) ENGINE=InnoDB;
