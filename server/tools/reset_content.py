"""Empty the content tree and everything anchored to it, keeping the devices and their parents.

    python3 -m server.tools.reset_content --dsn mysql://... --dry-run
    python3 -m server.tools.reset_content --dsn mysql://... --yes

This exists for the 2026-11 rebuild only: the diagnostic-node tree replaces the old topic tree
wholesale, so every question, every answer and every mastery row points at a topic that is about to
stop existing. The decisions behind it are in the plan (`docs/PLAN-上线与题库.md` 姊妹篇,
`docs/RULES.md` §2): the old data is not migrated.

Kept on purpose:

* `device`, `account*`, `config*`, `term_anchor` — the families using this are the same families;
* `daily_state` — today's attempt count and earned minutes are the child's, not the content's. Its
  mix snapshot is cleared instead, because the snapshot was taken under the old model.

Everything deleted is written to `local/backup/<stamp>/<table>.json` first (row by row, `default=str`
for datetimes and decimals). `--dry-run` prints the counts and writes nothing; `--yes` is required to
delete, because the FK graph means a half-run leaves a database that can neither serve quiz questions
nor be rolled back by re-running this tool.

Order is child → parent, so the foreign keys hold at every step on MySQL.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from server.app.db import Database  # noqa: E402

#: `QUIZ_BACKUP_DIR` exists so a test run can point the dump at a temp dir instead of `local/`.
BACKUP_DIR = Path(
    os.environ.get("QUIZ_BACKUP_DIR")
    or Path(__file__).resolve().parent.parent.parent / "local" / "backup"
)

#: Deleted in this order. Comments say what the rows are, so a future reader knows what was thrown away.
TABLES: list[tuple[str, str]] = [
    ("wrong_item", "错题本：TV 与手录试卷的每一道错题"),
    ("mastery_event", "掌握度流水账"),
    ("topic_mastery", "每设备 × 每知识点的掌握度"),
    ("topic_signal", "每设备 × 每知识点的对错计数"),
    ("curriculum_progress", "每设备 × 每册的当前单元"),
    ("quiz_answer", "每组题的逐题作答（挂在旧题目 id 上）"),
    ("gen_task", "异步生成队列（从未写入过）"),
    ("ticket", "已签发的换时长凭证（审计用）"),
    ("quiz_session", "出题会话"),
    ("paper", "家长手录的试卷"),
    ("question_option", "选择题选项"),
    ("question_answer", "填空题可接受答案"),
    ("question_topic", "一题多知识点（从未写入过）"),
    ("question", "题目"),
    ("topic", "知识点（旧树）"),
    ("unit", "单元 / 章节"),
    ("textbook", "册"),
]

#: Cleared rather than deleted: the row is still the child's day, only its mix snapshot is stale.
CLEARED_COLUMNS = (
    "mix_state", "mix_c", "mix_r", "mix_n", "mix_deepest", "mix_note",
)


def counts(db: Database) -> list[tuple[str, str, int]]:
    out = []
    for table, what in TABLES:
        try:
            out.append((table, what, int(db.scalar(f"SELECT COUNT(*) FROM {table}", default=0) or 0)))
        except Exception:  # a table this database never had is not a reason to stop
            out.append((table, what, -1))
    return out


def dump(db: Database, rows_by_table: dict[str, list[dict]], stamp: str) -> Path:
    target = BACKUP_DIR / stamp
    target.mkdir(parents=True, exist_ok=True)
    for table, rows in rows_by_table.items():
        path = target / f"{table}.json"
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    summary = {table: len(rows) for table, rows in rows_by_table.items()}
    (target / "index.json").write_text(
        json.dumps({"at": stamp, "tables": summary}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="empty the content tree and the mastery it anchors")
    parser.add_argument("--dsn", default=None, help="explicit DSN; production is passed by hand")
    parser.add_argument("--dry-run", action="store_true", help="print the counts, change nothing")
    parser.add_argument("--yes", action="store_true", help="actually delete")
    args = parser.parse_args(argv)

    if not args.dry_run and not args.yes:
        print("refusing to delete without --yes (use --dry-run to look first)", file=sys.stderr)
        return 2

    db = Database(args.dsn)
    db.connect()
    try:
        table_counts = counts(db)
        width = max(len(t) for t, _, _ in table_counts)
        for table, what, count in table_counts:
            mark = "missing" if count < 0 else str(count)
            print(f"  {table:<{width}}  {mark:>7}  {what}")
        total = sum(c for _, _, c in table_counts if c > 0)
        print(f"  {'total':<{width}}  {total:>7}")

        if args.dry_run:
            print("dry run: nothing written, nothing deleted")
            return 0

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        rows_by_table: dict[str, list[dict]] = {}
        for table, _, count in table_counts:
            if count > 0:
                rows_by_table[table] = db.query(f"SELECT * FROM {table}")
        target = dump(db, rows_by_table, stamp)
        print(f"backup: {target}")

        for table, _, count in table_counts:
            if count > 0:
                db.execute(f"DELETE FROM {table}")
                print(f"  deleted {count} from {table}")

        assignments = ", ".join(f"{column} = NULL" for column in CLEARED_COLUMNS)
        cleared = db.execute(f"UPDATE daily_state SET {assignments}")
        print(f"  cleared today's mix snapshot on {cleared} daily_state row(s)")
        print("reset: done (subject rows kept; run import_curriculum and import_questions next)")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
