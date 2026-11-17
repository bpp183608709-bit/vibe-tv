#!/bin/sh
# Let's Encrypt 续期之后，把家长页那张叶子证书搬进应用目录，再重启服务。
# 电视那条（9443）用的是 tls/server.pem + APK 内置 CA，这里不碰。
#
# 安装位置：/etc/letsencrypt/renewal-hooks/deploy/90-quiz-h5.sh（指向本文件的符号链接）。
# certbot 每次续期成功都会执行它；`--dry-run` 也会执行，所以下面的「内容没变就不动」不是优化，
# 是防止模拟续期把正在服务的进程打断。
#
# 为什么必须在这个位置：续期只更新 /etc/letsencrypt/live/ 里的文件，服务读的是 /opt/wrong-quiz/tls/。
# 没有这个钩子，证书续了也是白续——线上会一直发旧的，到期那天手机开始弹警告。
set -eu
LIVE="${RENEWED_LINEAGE:-/etc/letsencrypt/live/47.95.198.148}"
DEST=/opt/wrong-quiz/tls
LOG=/var/log/wrong-quiz-cert.log

# 同样的字节不重启：certbot 一天检查两次，dry-run 也会走到这里。
if cmp -s "$LIVE/fullchain.pem" "$DEST/h5.pem" && cmp -s "$LIVE/privkey.pem" "$DEST/h5.key"; then
  echo "$(date -Is) unchanged, nothing to do" >> "$LOG"
  exit 0
fi

install -m 644 -o wrongquiz -g tvault "$LIVE/fullchain.pem" "$DEST/h5.pem"
install -m 600 -o wrongquiz -g tvault "$LIVE/privkey.pem" "$DEST/h5.key"
systemctl restart wrong-quiz
sleep 2

# 换完证书要证明服务还活着：发不出去的证书和过期证书一样糟，但这一条能在日志里查。
if systemctl is-active --quiet wrong-quiz; then
  echo "$(date -Is) deployed, notAfter=$(openssl x509 -in "$DEST/h5.pem" -noout -enddate | cut -d= -f2)" >> "$LOG"
else
  echo "$(date -Is) DEPLOYED BUT SERVICE IS DOWN" >> "$LOG"
  exit 1
fi
