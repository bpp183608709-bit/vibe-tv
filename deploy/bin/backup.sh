#!/bin/sh
# 每日备份：mysqldump 全库 → gzip → 留在本机 7 天。
#
# 用法（cron，root）：
#     15 3 * * *  /opt/wrong-quiz/bin/backup.sh >> /var/log/wrong-quiz-backup.log 2>&1
#
# 三条原则：
#   * **备份在 root 名下，不在服务账号下。** 服务账号能写的地方，删库的人也能删；备份要和被备份的东西分开。
#   * **失败要吵。** 空文件、0 字节、mysqldump 非零退出，全部 exit 非零——静默失败的备份等于没有备份。
#   * **只留 7 天，多的删掉。** 磁盘是 40G，一个库用不到 1M，但没人清理的目录总会长到把盘写满。
set -eu

DB="${QUIZ_DB:-quiz}"
DIR="${QUIZ_BACKUP_DIR:-/var/backups/wrong-quiz}"
KEEP_DAYS=7
STAMP=$(date +%Y%m%d-%H%M%S)
OUT="$DIR/$DB-$STAMP.sql.gz"

umask 077
mkdir -p "$DIR"

# --single-transaction: 不锁表，备份期间孩子可以照常做题。
# --routines --triggers: 现在没有存储过程和触发器，但备份不该因为"暂时没有"而少备份一类对象。
mysqldump --single-transaction --routines --triggers --default-character-set=utf8mb4 \
    "$DB" | gzip -9 > "$OUT"

# gzip 对空输入也会成功，所以查的是解压后的字节数，不是退出码。
BYTES=$(gzip -dc "$OUT" | wc -c)
if [ "$BYTES" -lt 10000 ]; then
    echo "FAIL: $OUT 只有 $BYTES 字节，像是空库，保留这份但退出非零" >&2
    exit 1
fi

find "$DIR" -name "$DB-*.sql.gz" -type f -mtime "+$KEEP_DAYS" -delete
echo "ok $OUT $BYTES bytes, $(find "$DIR" -name "$DB-*.sql.gz" | wc -l) kept"
