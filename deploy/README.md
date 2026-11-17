# 上传：从一台空机器到孩子能做题

> 这台机器：阿里云 ECS，Ubuntu 22.04.5，2 vCPU / 1.6 GB，40 GB 盘，公网 `47.95.198.148`，
> 域名 `quiz.qust.me`（备案中，见 §7）。所有命令都以 root 执行，`#` 后面的注释是「为什么」。

---

## 0. 这套部署长什么样

**一个进程，三个监听。** 8443 给家长手机（要找公信证书），9443 给电视（它钉的是自家 CA），
8100 只给本机 curl 和运维脚本。

**没有 nginx。** 一开始计划里有一层反向代理，后来去掉了：代理要买的是「TLS 集中、限速、静态缓存」，
而这个规模的收益小于它带来的一个额外进程、一份额外配置和一类额外故障。TLS 现在在 Python 里
（两个端口两套证书就是一个参数），限速在配对接口里，静态文件就三个。真正需要代理的那天
（多机、灰度、HTTP/2）再回来加，那时它是升级不是返工。

**密钥不在任何文件里。** `DEEPSEEK_API_KEY` 由 TinyVault 在进程启动前注入环境；数据库账号是
MySQL 的 `auth_socket`，本机 socket 上内核已经验过 uid，所以 DSN 里没有密码可写、可漏。

```
/etc/systemd/system/wrong-quiz.service   ← deploy/systemd/wrong-quiz.service
/usr/local/bin/tvault-run.sh             ← TinyVault 的解锁壳（不属于本仓库）
/etc/tvault/wrong-quiz-pass.env          ← 0400 root，主密码
/opt/wrong-quiz/
    bin/app                              前台 exec 入口（deploy/bin/app）
    bin/backup.sh  bin/healthcheck.sh    运维脚本（deploy/bin/）
    deploy.env                           非密钥参数（deploy/env.example）
    server/                              python 代码 + 两个 DDL + 工具
    web/                                 家长页三个文件
    .venv/                               python3.10 + pymysql + sympy
    secrets/ticket-signing-key.pem       ES256 私钥，丢了未兑换的票全作废
    tls/                                 ca.pem / server.pem / server.key
/var/backups/wrong-quiz/                 每日 mysqldump
```

---

## 1. 系统与 MySQL

```bash
apt update && apt install -y python3.10-venv mysql-server
cp deploy/mysql/zz-quiz.cnf /etc/mysql/mysql.conf.d/     # 1.6G 内存的调优，见文件头
systemctl restart mysql
```

建库建号（**没有密码**，因为用 `auth_socket`）：

```sql
CREATE DATABASE quiz CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'wrongquiz'@'localhost' IDENTIFIED WITH auth_socket;
GRANT ALL PRIVILEGES ON quiz.* TO 'wrongquiz'@'localhost';
-- 验收专用的空库，见 §4
CREATE DATABASE quiz_acceptance CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
GRANT ALL PRIVILEGES ON quiz_acceptance.* TO 'wrongquiz'@'localhost';
```

表结构与内容：

```bash
mysql -uroot quiz < server/db/schema.sql                     # 26 表 216 列
su -s /bin/sh wrongquiz -c '.venv/bin/python -m server.tools.import_curriculum --dsn "$DSN"'
su -s /bin/sh wrongquiz -c '.venv/bin/python -m server.tools.gen_seed_questions --per-topic 5 --dsn "$DSN"'
```

`DSN` 是 `mysql://wrongquiz@localhost/quiz?unix_socket=/run/mysqld/mysqld.sock`。
**必须用 `su wrongquiz` 跑**：`auth_socket` 认的是 uid，root 连不上这个账号——这不是权限配错，
是这套设计故意让「能写库的进程」和「能管机器的人」分开。

旧库升级用 `server/tools/migrate.py`（只加列，不删不改，可以重复跑）：

```bash
.venv/bin/python -m server.tools.migrate --dsn "$DSN"      # 打出来要执行的 ALTER
mysql -uroot quiz -e "ALTER TABLE device ADD COLUMN secret_hash CHAR(64) NULL"   # 生产手工执行
```

---

## 2. 部署账号与目录

```bash
useradd --system --shell /usr/sbin/nologin wrongquiz
groupadd -r tvault && usermod -aG tvault wrongquiz
mkdir -p /opt/wrong-quiz /var/lib/tvault/wrong-quiz /var/backups/wrong-quiz
chown -R wrongquiz:tvault /opt/wrong-quiz /var/lib/tvault/wrong-quiz
chmod 750 /var/backups/wrong-quiz
```

代码与虚拟环境：

```bash
cd /opt/wrong-quiz
python3.10 -m venv .venv && .venv/bin/pip install pymysql sympy
# 代码用 scp/rsync 推上来，或 git clone；只要 server/ web/ 在 APP_DIR 下、属主是 wrongquiz
cp deploy/env.example deploy.env && chown wrongquiz:tvault deploy.env && chmod 640 deploy.env
```

---

## 3. 证书与密钥

**一张 CA，一张叶子，两个用途。** CA 的私钥只在签证书的机器上，`ca.pem` 随 APK 走。

```bash
sh server/tools/make_ca.sh quiz.qust.me 47.95.198.148   # 每个参数一个 SAN，第一个兼作 CN
# 产物在 server/local/tls/：ca.pem（公开）、server.pem + server.key（0600）、ca.key（收好）
# 装过去：
install -m 644 server/local/tls/ca.pem  /opt/wrong-quiz/tls/ca.pem
install -m 644 server/local/tls/server.pem /opt/wrong-quiz/tls/server.pem
install -m 600 server/local/tls/server.key /opt/wrong-quiz/tls/server.key
```

域名和 IP 都进 SAN，所以 DNS 指过来之前用地址、之后用域名，**是同一张证书**。
重新生成 CA 必须重装 APK：电视钉的是旧那张。

票签名密钥：

```bash
.venv/bin/python -m server.tools.genkey --out secrets/     # 写出 secrets/ticket-signing-key.pem
chmod 600 secrets/ticket-signing-key.pem
```

保险库里的模型 Key（由 `tvault` 管理，本仓库不碰）：

```bash
tvault put wrong-quiz DEEPSEEK_API_KEY sk-...
# 自检：重启后 /healthz 里 "key_present": true
```

---

## 4. 起来

```bash
cp deploy/systemd/wrong-quiz.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now wrong-quiz
systemctl status wrong-quiz --no-pager
journalctl -u wrong-quiz -n 30 --no-pager
```

四条自检（前两条在本机，后两条从你笔记本上打）：

```bash
curl -s localhost:8100/readyz                                   # status ok, db true
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8100/v1/quiz/next -d '{"device_id":"x"}'
                                                                # 401 —— 没有设备密钥就该拒
curl -sk https://47.95.198.148:8443/ | head -3                  # 家长页 HTML
curl -sk -X POST https://47.95.198.148:9443/v1/pair/start -d '{"app_version":"manual"}'
                                                                # device_id + device_secret + pair_code
```

验收全套（**必须指向空库**，脚本现在会自己拒绝非空库）：

```bash
sh server/tools/acceptance_mysql.sh        # 自己重建空库，然后跑全量
# 期望：acceptance: 217/217 passed  (db quiz_acceptance)
```

`server/tools/check_schema_parity.py --mysql "$DSN"` 会比真库与 SQLite DDL 的列；不等就是有人改了
一边没改另一边。

---

## 5. 备份与恢复

```bash
install -m 755 deploy/bin/backup.sh deploy/bin/healthcheck.sh /opt/wrong-quiz/bin/
chown root:root /opt/wrong-quiz/bin/backup.sh /opt/wrong-quiz/bin/healthcheck.sh
( crontab -l 2>/dev/null; echo '15 3 * * * /opt/wrong-quiz/bin/backup.sh >> /var/log/wrong-quiz-backup.log 2>&1';
  echo '*/10 * * * * /opt/wrong-quiz/bin/healthcheck.sh || logger -t wrong-quiz "healthcheck failed"' ) | crontab -
```

**恢复演练**（不做演练的备份等于没有备份；做完下面这四条才算数）：

```bash
mysql -uroot -e "DROP DATABASE IF EXISTS quiz_restore; CREATE DATABASE quiz_restore"
gunzip -c /var/backups/wrong-quiz/quiz-*.sql.gz | mysql -uroot quiz_restore
mysql -N -uroot -e "select count(*) from quiz_restore.question"      # 与生产一致
mysql -uroot -e "DROP DATABASE quiz_restore"
```

备份在 root 名下，不在服务账号下：服务账号能写的地方，删库的人也能删。

---

## 6. 安全组

只开四个口，其余全关：

| 端口 | 用途 | 来源 |
|---|---|---|
| 22 | SSH | 你的固定 IP，**不要 0.0.0.0/0** |
| 8443 | 家长页（https） | 0.0.0.0/0 |
| 9443 | 电视（https，钉 CA） | 0.0.0.0/0 |
| 443 | 备案下来后的家长页 | 0.0.0.0/0 |

MySQL 只听 127.0.0.1（`zz-quiz.cnf` 的 `bind-address`），所以 3306 既不开也不该开。

---

## 7. 备案没下来之前

8443 上现在是自签证书，手机点开会有一次警告——这就是「能用但不好用」。备案下来之后：

1. `certbot certonly --standalone -d quiz.qust.me`，拿 `/etc/letsencrypt/live/quiz.qust.me/`；
2. 把两个文件软链到 `/opt/wrong-quiz/tls/`，在 `deploy.env` 里加
   `QUIZ_H5_CERT=… QUIZ_H5_KEY=…`（`deploy/bin/app` 已经认这两个变量），`systemctl restart wrong-quiz`；
3. 加一条 `--listen 0.0.0.0:443:…`，或者把 8443 改成 443——**APK 不用动**，因为它钉的是 CA，
   CA 没换，域名换了也照旧验得过。

备案没通过时的退路写在同一份文件里：`docs/备案降级.md`。
