#!/bin/sh
# 在服务器上对**空库**跑一遍全量验收（MySQL）。
#
#     sh server/tools/acceptance_mysql.sh
#
# 为什么要这个脚本：验收脚本自己会喊「拒绝在非空库上跑」，但把 DSN 写对仍然容易出错——
# 上一次就是有人从 deploy.env 抄了 `QUIZ_DSN`（= 生产库），于是 207 条验收开始往生产库里写账号、
# 写假电视、写假答案，然后拿自己的残留当失败原因排查了半天。所以空库这件事在这里先做掉。
#
# 要求：以 root 跑（要 drop/create 验收库），服务账号是 wrongquiz（auth_socket，认 uid）。
set -eu

DB="${QUIZ_ACCEPTANCE_DB:-quiz_acceptance}"
APP="${QUIZ_APP_DIR:-/opt/wrong-quiz}"
DSN="mysql://wrongquiz@localhost/$DB?unix_socket=/run/mysqld/mysqld.sock"

mysql -uroot -e "DROP DATABASE IF EXISTS $DB"
mysql -uroot -e "CREATE DATABASE $DB CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
mysql -uroot -e "GRANT ALL ON $DB.* TO 'wrongquiz'@'localhost'"
mysql -uroot "$DB" < "$APP/server/db/schema.sql"

cd "$APP"
su -s /bin/sh wrongquiz -c "cd $APP && QUIZ_ACCEPTANCE_LOG=/tmp/acceptance-server.log \
    .venv/bin/python -u -m server.tools.acceptance --dsn '$DSN'"
