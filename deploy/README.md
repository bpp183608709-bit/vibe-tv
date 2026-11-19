# 上传：从一台空机器到孩子能做题

> 这台机器：阿里云 ECS，Ubuntu 22.04.5，2 vCPU / 1.6 GB，40 GB 盘，公网 `47.95.198.148`，
> 域名 `vibedemo.cn`，家长页 `tvparent.vibedemo.cn`（备案 2026-11-19 通过，见 §7）。所有命令都以 root 执行，`#` 后面的注释是「为什么」。

---

## 0. 这套部署长什么样

**一个进程，三个监听。** 443 给家长手机（要找公信证书），9443 给电视（它钉的是自家 CA），
8100 只给本机 curl 和运维脚本。

**手机上的家长页只走 443。** 9443 那张证书是本机自签 CA 签的（签发者 `Aerial Quiz CA`，SAN 里是这台机器的 IP），手机的系统信任库里没有它，打开必弹「证书不受手机操作系统信任」——那张是给电视钉的。
两个端口服务同一套路由，所以**打错端口页面照样能进**，这正是它容易被误用的地方：看到警告别急着重装证书，
先看地址栏的端口。备案前后临时用过 8443，现在 `QUIZ_H5_PORT` 是 443，8443 只在回退时启用。

**没有 nginx。** 一开始计划里有一层反向代理，后来去掉了：代理要买的是「TLS 集中、限速、静态缓存」，
而这个规模的收益小于它带来的一个额外进程、一份额外配置和一类额外故障。TLS 现在在 Python 里
（两个端口两套证书就是一个参数），限速在配对接口里，静态文件就三个。真正需要代理的那天
（多机、灰度、HTTP/2）再回来加，那时它是升级不是返工。

**TLS 握手有 5 秒上限。** `api.py` 的 `_bind` 不再把监听 socket 直接 `wrap_socket`——那种写法把握手
塞进 `accept()` 且没有超时，一条只连不发数据的连接（扫描器、握手中途被掐断的浏览器）就能把这个端口
之后的所有请求堵在 backlog 里。2026-11-17 家长页整个不可用就是这条：连接不断，请求全超时。
现在先 accept、再带 `HANDSHAKE_TIMEOUT` 换 TLS，握不完就丢掉这一条。**症状再看一遍**：某个端口
`ss -lnt` 的 `Send-Q` 一直等于 `Recv-Q` 且堆满，就是 accept 被钉住了。

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
mysql -uroot quiz < server/db/schema.sql                     # 27 表 259 列
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

### 家长页那张公信证书（2026-11-17 起用 IP 版，2026-11-19 换成域名版）

> 下面这段是 IP 证书时期的做法，留作历史与降级参考。现在家长页用的是
> `tvparent.vibedemo.cn` 的域名证书，签发与切换步骤见 §7；两者在 `cert-hook.sh` 眼里
> 是同一条终点（`tls/h5.pem`），区别只在 `LIVE=` 指向哪条 lineage。

8443 给手机看，手机不认我们的 CA，所以它单独用一张 Let's Encrypt 的 **IP 证书**：
免费、不需要域名、不需要备案，但只有 **160 小时（约 6 天）**，必须自动续期。

```bash
apt-get remove -y certbot                      # 22.04 自带的是 1.21，不支持 profile
snap install --classic certbot                 # 5.x，认 --preferred-profile 与 IP 标识符
/snap/bin/certbot certonly --standalone --ip-address 47.95.198.148 \
    --preferred-profile shortlived --agree-tos --register-unsafely-without-email --non-interactive
```

两处容易踩：

- **标识符要用 `--ip-address`，不是 `-d`**。用 `-d 47.95.198.148` 会得到
  「The Let's Encrypt certificate authority will not issue certificates for a bare IP address」。
- **验证只能走 80 或 443**（`http-01` / `tls-alpn-01`，IP 没有 dns-01）。安全组必须开 80，
  否则续期一开始就失败。内地 ECS 的 80 有可能被拦到备案页，先放个文件从外网 curl 一次确认。

续期由 `bin/cert-hook.sh` 收尾：把 `fullchain.pem` / `privkey.pem` 搬成
`tls/h5.pem`（644）和 `tls/h5.key`（600），属主 `wrongquiz:tvault`，然后重启服务。

**它必须装成 certbot 的 deploy hook，否则续期等于白续**：续期只更新
`/etc/letsencrypt/live/` 里的文件，服务读的是 `/opt/wrong-quiz/tls/`。没有钩子，证书换了线上还在发旧的，
6 天到期那天手机才开始弹警告。2026-11-18 之前就是这样——renewal 配置里没有 `renew_hook`，
`renewal-hooks/deploy/` 也是空的，文档里却写着"已经挂上了"。

```bash
ln -sf /opt/wrong-quiz/bin/cert-hook.sh /etc/letsencrypt/renewal-hooks/deploy/90-quiz-h5.sh
snap run certbot renew --dry-run --run-deploy-hooks      # 必须带这个参数才会真的跑钩子
tail -3 /var/log/wrong-quiz-cert.log                     # 期望 "unchanged, nothing to do"
```

`--dry-run` 默认**不执行** deploy hook，只有 `--run-deploy-hooks` 才会；不加就验不出接线对不对。
钩子里有「字节没变就不重启」的判断，所以一天两次的检查和 dry-run 都不会打断正在服务的进程。
真换证时日志会写 `deployed, notAfter=...`；服务起不来则写 `DEPLOYED BUT SERVICE IS DOWN` 并返回非零。

`snap.certbot.renew.timer` 一天跑两次，对 6 天的证书够用。

改完 `bin/app` 记得**重新推上去**——2026-11-17 这次就是仓库里早就有 `H5_CERT` 支持、
机器上还是旧脚本，换了证书却一直在用旧那张，排查了半小时。

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
curl -sI https://tvparent.vibedemo.cn/ | head -1               # 家长页 HTML（443）
curl -sk https://47.95.198.148:8443/ | head -3                  # 仅回退到 8443 时才有监听
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
| 80 | 只为签发/续期证书时 ACME 校验用，平时无监听 | 0.0.0.0/0 |
| 443 | 家长页（https，Let's Encrypt 域名证书） | 0.0.0.0/0 |
| 9443 | 电视（https，钉 CA） | 0.0.0.0/0 |

80 平时是空的（`certbot --standalone` 只在续期那几秒绑它），但**安全组必须一直开着**：
关掉之后某天续期静默失败，六天后所有手机又开始报证书警告。

MySQL 只听 127.0.0.1（`zz-quiz.cnf` 的 `bind-address`），所以 3306 既不开也不该开。

---

## 7. 域名与 443（备案 2026-11-19 通过）

家长页地址从 `https://47.95.198.148:8443` 换成 **`https://tvparent.vibedemo.cn`**（443，不带端口）。
APK 不用重出：电视走 9443，钉的是自家 CA，跟域名无关。

**顺序不能换，先 DNS 后证书**——`http-01` 是 Let's Encrypt 回连域名，解析没生效时签发必然失败。

```bash
# 0) 前置：A 记录 tvparent.vibedemo.cn → 47.95.198.148；安全组放行 443 与 80。
dig +short tvparent.vibedemo.cn          # 必须是这台机器的公网 IP

# 1) 签域名证书（80 只在签发这几秒被 certbot 占用，服务不监听 80，不冲突）
/snap/bin/certbot certonly --standalone -d tvparent.vibedemo.cn \
    --agree-tos --register-unsafely-without-email --non-interactive

# 2) 首次签发不会触发 deploy hook，手工跑一次，把证书搬进应用目录
RENEWED_LINEAGE=/etc/letsencrypt/live/tvparent.vibedemo.cn /opt/wrong-quiz/bin/cert-hook.sh

# 3) 换端口：deploy.env 里 QUIZ_H5_PORT=443（模板已改），单元文件补 CAP_NET_BIND_SERVICE
scp deploy/systemd/wrong-quiz.service root@47.95.198.148:/etc/systemd/system/
systemctl daemon-reload && systemctl restart wrong-quiz

# 4) 从外网验（不要带 -k，要的是系统信任链通过）
curl -sI https://tvparent.vibedemo.cn/ | head -1        # HTTP/1.1 200 OK
openssl s_client -connect tvparent.vibedemo.cn:443 -servername tvparent.vibedemo.cn \
  </dev/null 2>/dev/null | openssl x509 -noout -subject -enddate
```

**已执行（2026-11-19 15:57 CST）**：A 记录生效后签发成功，证书 `CN=tvparent.vibedemo.cn`、到期
2027-02-17；`deploy.env` 已改 443，**8443 不再监听**；旧 IP 证书 `47.95.198.148` 已 `certbot delete`，
钩子只剩域名一条 lineage。外网验证 `GET /` → 200、`/v1/auth/me` → 401、9443 仍 200。
下面的命令保留为「换机器 / 换域名」时的手册，不是待办。

演练续期时务必带 `--no-random-sleep-on-renew`：`certbot renew` 默认随机等最多 8 分钟，
不加这个参数会误判成卡死（本次就被它坑过一次，还留下了 `.certbot.lock`，需手工清）。

**为什么需要改 systemd 单元**：服务以 `wrongquiz` 非 root 运行，绑定 1024 以下端口会拿到 `EACCES`，
日志里是「无法绑定 0.0.0.0:443（Permission denied）」。单元里那两行 `AmbientCapabilities` /
`CapabilityBoundingSet` 只授「绑定低端口」这一项能力，不会把进程变成 root，也与 `NoNewPrivileges` 不冲突。

**旧 IP 证书已删（2026-11-19）**：`certbot delete --cert-name 47.95.198.148`。留着它不会覆盖域名证书
（`cert-hook.sh` 按 `RENEWED_LINEAGE` 只认域名那条），但它每 6 天续一次、每次刷日志，没有收益。

**回退**：`deploy.env` 改回 `QUIZ_H5_PORT=8443` + 重启即可，证书不用动、APK 不用动。unit 里那两行能力留着无害。

**换域名后有两件事不会自动迁移**：

1. **手机要重新配对一次。** 会话 cookie 是 host-only（`Path=/`，没有 `Domain`），针对 `47.95.198.148` 发的
   cookie 不会带到新域名。电视上出配对码、手机输一次即可，历史数据全在库里，不受影响。
2. **PWA 桌面图标要重新添加。** manifest 的 `start_url`/`scope` 是相对的，但图标的 origin 变了，
   旧图标指向 IP，点开会回到旧地址。

备案没通过时的降级方案（IP + IP 证书）保留在 `docs/备案降级.md`，只作历史记录。
