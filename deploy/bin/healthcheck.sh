#!/bin/sh
# 探活：三个问题，一次问完。
#
#     1. 服务还活着吗（/readyz 说 ok，不是 degraded）
#     2. 数据库还答得上来吗（表在不在、行数对不对）
#     3. 最近一次备份新鲜吗（超过 26 小时就是没人跑 cron）
#
# 用法（cron，root，每 10 分钟）：
#     */10 * * * * /opt/wrong-quiz/bin/healthcheck.sh || logger -t wrong-quiz "healthcheck failed"
#
# 退出码：0 全好，1 有一项不对。**检查本身不告警**——对接哪个通知渠道是部署方的事，
# 这个脚本只负责把「不对」变成非零退出码，让 cron / systemd timer / 监控系统去看见。
set -eu

BASE="${QUIZ_HEALTH_URL:-http://127.0.0.1:8100}"
BACKUP_DIR="${QUIZ_BACKUP_DIR:-/var/backups/wrong-quiz}"
MAX_BACKUP_AGE_H="${QUIZ_MAX_BACKUP_AGE_H:-26}"
BAD=0

say() { printf '%-8s %s\n' "$1" "$2"; }

# 1. 探活。curl 的 -f 让 5xx 直接非零；--max-time 免得探针自己卡住。
if BODY=$(curl -sf --max-time 5 "$BASE/readyz"); then
    case "$BODY" in
        *'"status": "ok"'*|*'"status":"ok"'*) say ok "readyz $BODY" ;;
        *) say FAIL "readyz 说 $BODY"; BAD=1 ;;
    esac
else
    say FAIL "readyz 没有应答（$BASE）"; BAD=1
fi

# 2. 数据库：连不上就说明 DSN 或 MySQL 出了问题，和探活失败是两件事，分开报。
if COUNTS=$(mysql -N -uroot -e "select count(*) from quiz.question" 2>/dev/null); then
    [ "$COUNTS" -gt 0 ] || { say FAIL "题库是空的"; BAD=1; }
    [ "$COUNTS" -gt 0 ] && say ok "题库 $COUNTS 题"
else
    say FAIL "数据库连不上"; BAD=1
fi

# 3. 备份新鲜度。用 find 的 -mmin 而不是解析文件名：文件名里没有时区，也没有人保证它写对。
if [ -d "$BACKUP_DIR" ]; then
    FRESH=$(find "$BACKUP_DIR" -name '*.sql.gz' -mmin "-$((MAX_BACKUP_AGE_H * 60))" | head -1)
    if [ -n "$FRESH" ]; then
        say ok "最近备份 $FRESH"
    else
        say FAIL "${MAX_BACKUP_AGE_H} 小时内没有新备份（$BACKUP_DIR）"; BAD=1
    fi
else
    say FAIL "备份目录不存在：$BACKUP_DIR"; BAD=1
fi

exit "$BAD"
