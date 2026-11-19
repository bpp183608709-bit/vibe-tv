# 交接：上线收尾与题库建设

> **用途**：新建一个对话接着做时，让新会话用最少的上下文接上现状。
> **用法**：把文末「交接提示词」里对应那一段（M3 题库 / 运维收尾 / M2 补齐）整段复制到新对话，其余内容由新会话自己读文件获得。
> **本文件是协作状态，不是规格。** 计划在 `docs/PLAN-上线与题库.md`，规则在 `docs/RULES.md`，接口在 `docs/QUIZ.md` / `docs/PARENTAL.md`，账号体系在 `docs/ACCOUNT.md`，决策在 `docs/待定事项-做题换时长.md`。
> 写于 2026-11-18 00:40，依据是 `git log` 到 `6563d87`、本地验收 **225/225**。

---

## 0. 三十秒现状

| 项 | 值 |
|---|---|
| 仓库 | 本地 `/Volumes/个人/code/aerial-tv-main`，远端 `github.com/bpp183608709-bit/vibe-tv`，分支 `main` |
| 同步状态 | 与远端 `main` 同步（2026-11-19 推送至 `1c6a7c2`） |
| 服务器 | 阿里云 ECS，Ubuntu 22.04，`47.95.198.148`，root 用 `~/.ssh/misty_deploy` 登录 |
| 服务 | systemd `wrong-quiz.service`，`/opt/wrong-quiz`，一进程三监听：**443 家长页（`tvparent.vibedemo.cn`）/ 9443 电视 / 8100 loopback**，没有 nginx。备案前家长页临时在 8443 |
| 数据库 | MySQL 8 `quiz`（生产）、`quiz_acceptance`（验收库，每次重建）、`quiz_restore`（恢复演练） |
| 证书 | 家长页用 Let's Encrypt **域名证书** `tvparent.vibedemo.cn`，**2027-02-17 06:58 GMT 到期**，`cert-hook.sh` 自动搬运；9443 用自签 CA，手机必弹警告，那是给电视钉的。旧 IP 证书已于 2026-11-19 `certbot delete` |
| 电视 | TCL `Aerial TV`，APK `versionCode=91`，已绑账号，做题→签票→兑换 30 分钟在公网跑通 |
| 验收 | 本地 SQLite **256/256**；MySQL 空库跑法见 §4 |
| 卡在哪 | 无阻断项。域名 + 443 已于 2026-11-19 切换完成（§6 第 4 条），剩余待办是备份 cron 未验证、认证屏缺返回（§6） |

---

## 1. 线上环境事实（都已在机器上验证过）

```
/opt/wrong-quiz/                 应用目录（属主 wrongquiz:tvault，750）
  bin/app                        systemd 的 ExecStart，读 QUIZ_* 环境变量
  bin/cert-hook.sh               把 LE 证书搬到 tls/h5.pem(644)/h5.key(600) 后 restart
  bin/backup.sh                  mysqldump | gzip，保留 7 天
  bin/healthcheck.sh             探活 + 题库行数 + 备份新鲜度
  tls/h5.pem, tls/h5.key         家长页证书（当前是 LE 那张）
  tls/tv-ca.pem 等               电视自签 CA，换它就要重装 APK
  deploy.env                     QUIZ_DSN / QUIZ_TICKET_KEY 等（不进仓库）
/etc/tvault/wrong-quiz-pass.env  主密码，0400 root
/var/backups/wrong-quiz/         文件备份 + 源码快照 code-<时间戳>/
/var/log/wrong-quiz*.log         服务、备份、探活日志
```

常用命令（在服务器上）：

```sh
systemctl status wrong-quiz --no-pager
journalctl -u wrong-quiz -n 80 --no-pager
curl -sk https://127.0.0.1:443/v1/auth/me -o /dev/null -w '%{http_code}\n'    # 401 = 活着（回退到 8443 时改端口）
sh /opt/wrong-quiz/bin/healthcheck.sh; echo $?
```

定时任务（`crontab -l`）：

```
15 3 * * *  /opt/wrong-quiz/bin/backup.sh      >> /var/log/wrong-quiz-backup.log
*/10 * * * * /opt/wrong-quiz/bin/healthcheck.sh
```

**证书续期**由 snap 的 `snap.certbot.renew.timer` 负责（每天 07:06 CST 跑）。签发命令当时用的是
`certbot certonly --standalone --ip-address 47.95.198.148 --preferred-profile shortlived`，续期配置在
`/etc/letsencrypt/renewal/47.95.198.148.conf`。

---

## 2. 最近这 12 个提交做了什么（倒序）

| 提交 | 内容 |
|---|---|
| `6563d87` | 换密保时显示「现在的问题」原文（本轮最后一个功能改动，已上线） |
| `dd9be98` / `952b256` | S2 清单第 13 步补规则、重出设置页截图 |
| `ec0a3a9` | 换密保必须先答对旧答案；补长度规则 UI；验收 217→223 |
| `9487352` | S2 手机真机验收清单（16 步） |
| `10038a3` | 安全头 4 条 + 静态 ETag（CSP、nosniff、Referrer-Policy、HSTS） |
| `640dfd5` | 文档：手机家长页只能走 8443 |
| `4b073b9` | `versionCode` 90→91 |
| `6bd71eb` | TLS 握手加 5 秒上限，半开连接不再钉死监听 |
| `2916643` | 家长页换 LE IP 证书，电视端证书不动 |
| `cf9b073` | M1 完成记录 |
| `5155d54` | 当贝安装通道失效，改走多屏互动 |

**已验证跑通的链路**：电视出题 → 作答 → 判分 → `quiz_session` submitted/score=5 → `ticket` 签发 →
电视本地验签兑换 → `dailyBudget 60000→1860000`。`ticket.redeemed_at` 为 NULL 是设计（电视不回执）。

---

## 3. 代码地图与四个坑

| 位置 | 职责 | 坑 |
|---|---|---|
| `server/app/api.py` | HTTP 路由、TLS 两个监听、静态文件 | ① 自定义 TLS 必须在 accept 之后 `settimeout` 再 `wrap_socket`，**不能把 `server.socket` 包一层**，否则一条半开连接钉死 accept；② 安全头方法不能叫 `_secure`，Handler 里已有同名方法会覆盖 |
| `server/app/auth.py` | 账号、会话、密保、找回、设备绑定 | `set_question()` 替换问题要 `old_answer`，错答与 `recover` **共用** `recover_fail_count`/`recover_locked_until`（5 次/15 分钟） |
| `server/app/h5.py` | 家长页接口（今天/设置/错题/周报/学习进度） | 配置写入要 bump `device_config.version`，电视靠版本号拉取 |
| `server/app/grade.py` `picker.py` `mix.py` `mastery.py` | 判分、选题配比、掌握度 | 规格在 `docs/WRONGBOOK.md`，改之前先读 |
| `server/web/` | 家长 H5，无构建步骤，`index.html + app.js + app.css` | 文案全在 `app.js` 顶部 `TEXTS`，**五种语言（zh/en/ru/uk/kk）必须同时加**，脚本里不许写死文字 |
| `server/tools/acceptance.py` | 端到端验收 | 只跑在**自建空库**上，`--dsn` 显式给，绝不指生产 |
| `deploy/` | unit、MySQL 参数、备份、探活、runbook、`bin/app` | ③ 换过证书或改过 `bin/app` 后，**服务器上那份可能还是旧版**，部署完要 `md5` 对一次 |
| `app/`（Android TV） | 出题页、本地验签、兑换 | 发 TCL 版本前先 `versionCode +1`；测试一律 release + `cmd package compile -m speed -f app.orchard.tv` |
| `lite/` | Android 4.x | 本功能不跟进 |

端口语义（易错）：**443 家长页（LE 域名证书，手机不警告）、9443 电视（自签，手机必警告）**。两个端口服务全部路由，
所以「打错端口也能打开，但一定弹证书警告」——看到警告先看端口，不要重装证书。

---

## 4. 怎么验（新会话第一步照抄）

```sh
cd /Volumes/个人/code/aerial-tv-main
node --check server/web/app.js                     # 前端语法
.venv/bin/python server/tools/acceptance.py        # 期望 225/225，约 20 秒，自动建新库
```

服务器上跑全量（MySQL，先重建空库）：

```sh
ssh -i ~/.ssh/misty_deploy root@47.95.198.148 'sh /opt/wrong-quiz/server/tools/acceptance_mysql.sh'
```

其他自检：`server/tools/check_schema_parity.py --mysql`（真库与 SQLite 列数一致）、
`server/tools/check_tv_silence.py`（电视端零表达扫描）。

出图（看界面，不是界面稿）：`(python3 -m http.server 8811 --directory server/web &) ; sleep 2; node server/tools/preview_h5.mjs`
产物在 `docs/mockups/live-*.png`。**这台机器上 headless Chrome 会偶发卡在 `Page.enable`（DevTools attach 超时），
重跑两三次才成一次；不要为此改脚本逻辑，先重跑。**

---

## 5. 下一步做什么（按依赖排序）

| 环节 | 现在能不能开工 | 第一天的输入 | 完成标志 |
|---|---|---|---|
| **运维收尾**（§6 第 1、2 条） | ✅ 随时 | 无 | 续期后 `tls/h5.pem` 自动更新且服务重启；备份连续 2 天有新文件 |
| **M2 剩余**（PWA 添加到主屏幕、域名） | ✅ | 无 | 手机从桌面图标打开无地址栏；`quiz.qust.me:8443` 直连不弹证书 |
| **M3 题库（一期 ~1000 题）** | ⛔ 缺素材 | **语文、英语课本目录页照片**、教材版本、考哪几科 | 26 批入库，抽检不合格率 ≤5% |
| **M4 增量生题** | ⛔ 依赖 M3 | — | 连续 7 天夜里自动补货，白天无库存告警 |
| **M5 拍照 OCR 录错题** | ⛔ 依赖 M2+M3 | 百炼 `qwen-vl-ocr` Key | 拍卷子→逐题确认→下一组出现该知识点 |

题库现状（2026-11-18 00:45 直接查生产库，不是抄旧文档）：**86 道题 / 30 个知识点 / 16 个单元**，
其中数学 8 个单元（齐）、**语文只有 4 个单元**、**英语只有 4 个单元**。表结构 26 表 216 列。
所以 M3 的瓶颈不是写代码，是目录核对——那件事只有户主能做。

> 注：`docs/PLAN-上线与题库.md` §1.2 里写的「30 个全部 `verified=false`」已经过时——当前库里
> 没有 `verified` 这类字段，别照着它改表。

---

## 6. 风险与待办（按紧急度）

1. **✅ 已修（2026-11-18 00:48）：证书续期现在有部署钩子。** 之前
   `/etc/letsencrypt/renewal-hooks/deploy/` 是空的，续期只更新 `/etc/letsencrypt/live/`，不会搬到
   `/opt/wrong-quiz/tls/h5.pem`。现在 `90-quiz-h5.sh` 指向 `/opt/wrong-quiz/bin/cert-hook.sh`（进仓库了），
   `snap run certbot renew --dry-run --run-deploy-hooks` 证明钩子被调用，日志见 `/var/log/wrong-quiz-cert.log`。
   **注意 `--dry-run` 默认不跑 deploy hook，必须加 `--run-deploy-hooks`，否则验不出接线。**
   下一个可观测点：域名证书 **2027-02-17 06:58 GMT 到期**，到期前续期应写出 `deployed, notAfter=...`。
   （2026-11-19 换域名后，旧 IP 证书那条 lineage 已删除，钩子只认 `tvparent.vibedemo.cn`。）
2. **🟡 备份 cron 从没在 03:00 跑过**（现有那份 `quiz-20261117-092055.sql.gz` 是手工的）。
   等一个凌晨或直接手动执行 `bin/backup.sh`，确认新文件 + `healthcheck.sh` 返回 0。
3. **🟡 认证屏没有「取消/返回」。** 设置页进「找回问题」「修改密码」后，除了浏览器返回没有出口。
   症状是用户被关在表单里。建议加一个返回箭头，回到设置页。
4. **✅ 2026-11-19：备案通过，家长页已切到域名。** `vibedemo.cn` 备案已下（阿里云协助、云解析托管），
   家长页走 `https://tvparent.vibedemo.cn`（443，LE 域名证书）。电视仍是 `https://47.95.198.148:9443`，APK 不用重出。
   切换步骤与回退见 `deploy/README.md` §7；注意手机上原有的配对被 cookie 绑在旧 IP 上，需要重新配对一次。

   实际执行记录（2026-11-19 15:57 CST）：

   - A 记录 `tvparent → 47.95.198.148` 生效后签证书，`certbot certonly --standalone`，到期 2027-02-17；
   - unit 补 `AmbientCapabilities=CAP_NET_BIND_SERVICE`（`systemctl show` 已确认 `cap_net_bind_service`），
     `deploy.env` 的 `QUIZ_H5_PORT` 8443 → 443，**8443 已不再监听**（外网 `ConnectionRefused`）；
   - `cert-hook.sh` 换成带 `RENEWED_LINEAGE` 过滤的版本，旧 IP 证书 `47.95.198.148` 已 `certbot delete`；
   - `certbot renew --dry-run --no-random-sleep-on-renew` 通过（默认的随机等待最多 8 分钟，演练时别省这个参数）；
   - 外网验证：`curl https://tvparent.vibedemo.cn/` → 200，`/v1/auth/me` → 401，证书 subject `CN=tvparent.vibedemo.cn`。
5. **✅ 2026-11-19：本地已推到远端 `main`（`1c6a7c2`）。** 之后每次改完记得 `git push`。
6. **🟢 出图脚本偶发卡死**（headless Chrome），已加 `showMain()` 防止连拍同一屏，复现率未根治。
7. **🟢 `S2-手机验收清单.md` 的 16 步还没有逐条打勾**——链路本身已被证明可用，缺的是记录。

---

### 6.1 待办：下次任何一次部署都要捎带重导题库（2026-11-19 记）

`knowledge-eval/data/` 在 2026-11-19 改了三科结构（语文换成 26 课、英语按 2026 秋新版重建、数学待改），**线上库还是旧内容**：`unit.name_zh` 仍是「艺术之美」「Cities in China」，缺英语 U9，语文 `d11/d12` 还挂着已删除的课文。这是数据与代码不同步，不是代码缺陷——只部署代码不会自愈，H5 报告会继续显示课本里没有的章节。

**不要单独跑，跟着下一次部署一起做。** 顺序不可颠倒：

```bash
# 1) 备份（先确认文件新鲜、gzip 能解开）
deploy/bin/backup.sh

# 2) 部署新代码之后，若 server/db/migrate-2026-11-19-flat-nodes.sql 还没执行过，先跑 DDL
mysql -uroot quiz < server/db/migrate-2026-11-19-flat-nodes.sql

# 3) 重导节点，再导题目（都按 business key upsert，可重复执行）
su -s /bin/sh wrongquiz -c '.venv/bin/python -m server.tools.import_curriculum --dsn "$DSN"'
su -s /bin/sh wrongquiz -c '.venv/bin/python -m server.tools.import_questions  --dsn "$DSN"'

# 4) 重启
systemctl restart wrong-quiz
```

**第 3 步之后必须处理 `english-6up-d05`。** 英语按新版重建时删掉了「There be 句型」这个诊断节点，但 `import_curriculum` 从不删 topic（删了会孤立作答记录），旧行会留在库里。先确认无人引用，再手工删：

```sql
SELECT t.id, t.code, t.name_zh FROM topic t WHERE t.code = 'english-6up-d05';
SELECT COUNT(*) FROM question      WHERE topic_id = (SELECT id FROM topic WHERE code='english-6up-d05'); -- 期望 0
SELECT COUNT(*) FROM topic_mastery WHERE topic_id = (SELECT id FROM topic WHERE code='english-6up-d05'); -- 期望 0
SELECT COUNT(*) FROM topic_signal  WHERE topic_id = (SELECT id FROM topic WHERE code='english-6up-d05'); -- 期望 0
DELETE FROM topic WHERE code = 'english-6up-d05';
```

三个计数都是 0 才能删；只要有一个非 0，就停下来问人，不要连带删作答记录。

（本次同时改了 `import_curriculum` 的残留检测：原来按 `ordinal` 比对，节点删除后序号被别人占用就静默放过；现在按 `code` 比对，缺节点会打印 `still in the database but gone from the file`，不再无声无息。）

验收（四条都要有输出，数字来自 `check_schema.py` 当前打印的 83 nodes / 46 questions）：

```sql
SELECT COUNT(*) FROM unit;      -- 期望 28（数学 10 + 语文 8 + 英语 10）
SELECT COUNT(*) FROM topic;     -- 期望 55
SELECT COUNT(*) FROM question;  -- 期望 46
SELECT name_zh FROM unit WHERE code='english-6up-u09';  -- 期望 I don't feel well
```

若上一步数学还没改（数学下架 3 个旧单元 + 拆 u09 是第 3 步），上面 28/55 要对不上，以当时 `check_schema.py` 的输出为准对账，不要照抄这四个数字。

---

## 7. 新会话必须遵守的纪律（从 `AGENTS.md` 抄来的，别重新讨价还价）

- **先写范围卡再动手**：目标一句话、改哪些文件、验收标准带数字、这次不做什么、前提。确认之后才改第一个字。
- **一次 commit 只做一件事**，提交信息里写清验收数从 X 到 Y。
- **验收只跑空库**，生产库只读；`--dsn` 从命令行给，不从 `deploy.env` 取。
- **没在真机/真服务上验过，不说「已完成」**。
- 不提交 `local/`（签名密钥、家里电视 IP）；不改户主数据。
- 改文案必须五种语言同时加。
- 调试失败两次就停手读代码，不许换端口/重跑脚本试探。

---

## 8. 交接提示词（复制这一段）

### 版本 A：M3 题库建设（最长的一条，缺素材就会卡）

```
接手 aerial-tv-main 的 M3 题库建设。先读 docs/HANDOFF-上线与题库.md、docs/PLAN-上线与题库.md §M3、
docs/QUIZ.md、docs/RULES.md，然后按 AGENTS.md 的规矩先给我一张范围卡（目标/改哪些文件/验收标准带数字/
这次不做什么/前提），我确认后再动手。

目标：把六年级上册语文、数学、英语的知识点树补齐（生产库现在 86 题 / 30 知识点 / 16 单元，
数学 8 个单元齐，语文 4 个、英语 4 个），然后按 docs/PLAN 的 26 批流水线入库，一期约 1000 题。

我这边会提供：语文和英语课本目录页照片、教材版本、这次要考哪几科。没有这些之前，你先只做
「导入链、五道闸、抽检脚本」这部分，不要凭记忆编造目录。

验收：server/tools/acceptance.py 225/225 不回归；导入脚本 --dry-run 能对一批真题报出
「入库几题、被哪道闸拦下几题」；抽检不合格率 ≤5%。
```

### 版本 B：运维收尾（半天，先做这个）

```
接手 aerial-tv-main 的线上运维收尾。先读 docs/HANDOFF-上线与题库.md §1 和 §6，然后给我范围卡。

要解决两件事：
1. Let's Encrypt IP 证书续期没有 deploy hook（/etc/letsencrypt/renewal-hooks/deploy/ 为空），
   证书 2026-11-23 20:42 GMT 到期。把 deploy/bin/cert-hook.sh 装成钩子（补可执行位、chown wrongquiz），
   用 snap run certbot renew --dry-run 证明「钩子执行 → 服务重启 → tls/h5.pem 的 notAfter 变新」。
2. 确认 bin/backup.sh 的 cron（每天 03:15）真的产出了新文件，healthcheck.sh 返回 0。

验收：dry-run 前后对比 tls/h5.pem 的 notAfter；/var/backups/wrong-quiz 出现当天新文件；
systemctl is-active wrong-quiz 仍为 active。别动业务代码，这次只碰部署件。
```

### 版本 C：M2 补齐（PWA + 认证屏返回）

```
接手 aerial-tv-main 的 M2 收尾。先读 docs/HANDOFF-上线与题库.md、docs/S2-手机验收清单.md、
docs/PLAN-上线与题库.md §M2，然后给我范围卡。

两件事：
1. 认证屏（找回问题/修改密码）没有取消或返回，用户除了浏览器返回没有出口——加一个返回设置页的箭头，
   五种语言文案同时加。
2. PWA：确认 manifest + 图标 + 独立窗口（S2 清单第 16 步），补 iOS 的 apple-touch-icon 与
   theme-color，保证「添加到主屏幕」后没有地址栏。

验收：acceptance.py 从 225 起只增不减；preview_h5.mjs 出新图证明返回键到位；手机真机从桌面图标打开
无地址栏。手机侧用 443（`https://tvparent.vibedemo.cn`），不要用 9443。
```

### 通用开头（不确定做什么时用这段）

```
接手 aerial-tv-main 的做题换时长项目。先读 docs/HANDOFF-上线与题库.md，再按需读它引用的
PLAN / RULES / QUIZ / ACCOUNT / 待定事项。读完先向我复述「现在的线上状态、下一个该做的环节、
需要我提供什么」，不要直接开始改代码。
```
