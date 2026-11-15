# server — 做题换时长的服务端

出题、判分、签票。**行为规格在 `docs/QUIZ.md` 与 `docs/PARENTAL.md`，错题训练的数值规则在 `docs/RULES.md`，
这里只讲怎么跑、目录怎么放、哪些还没做。**

现状（2026-11-16 复验）：P1/P2/P3 的链路都已跑通，错题训练 P0 的 A–E 已落地（`server/tools/acceptance.py` 158/158），
电视端已上机，手机页已在电视上真机走通「手机改设置 → 94 秒后电视应用」与「电视上报今日已看」。
包含：建表 → 知识点树导入 → 出题 → 判分 → 签票 → 电视端本地验签 → 钉住 CA 的 https →
六位码配对 → 设置下发 → 今日 / 错题本 / 周报。
**不含**：设备鉴权（`device_id` 仍在请求体里）、真实题库（现在是模板生成的种子题）、公网部署。

## 为什么是 Python，为什么没有框架

D7 你答「都不熟悉，你推荐为主」。选 Python 只有一个硬理由：**E6 要求用 sympy 把数学题再算一遍**。

依赖被砍到只剩一个 `pymysql`，原因是三个都自己写了：

| 常见依赖 | 这里用什么 | 为什么 |
|---|---|---|
| FastAPI + uvicorn | `http.server` | 四条路由、一个进程、2C2G。少一个进程要守护 |
| SQLAlchemy | `app/db.py` | 两套方言、一个事务助手，比学 ORM 便宜 |
| cryptography | `app/es256.py` | 本机装不上（要 Rust 工具链）；300 行标准库实现，用 openssl 验证互通 |
| PyYAML | 知识点树用 JSON | 少一个依赖，树本来就是脚本产的 |

## 目录

```
server/
  db/
    schema.sql           MySQL 8，生产事实来源
    schema.sqlite.sql    本机验收用的等价物；两份由 tools/check_schema_parity.py 比对防漂移
  content/curriculum/
    grade6-up.json       知识点树（三科 16 单元 30 知识点），verified=false 待 OCR 核对
  app/
    api.py               HTTP 路由：/health /v1/pair/{start,renew,status} /v1/config /v1/heartbeat
                         /v1/quota /v1/quiz/next /v1/quiz/{id}/grade /v1/auth/* /v1/h5/*，
                         以及 server/web/ 的静态文件
    auth.py              账号：注册、登录、会话、绑定表、注册四道闸、口令规则、改密与找回（docs/ACCOUNT.md）
    h5.py                家长手机那一半：配对、今日、设置读写、错题本、周报、心跳、配对限速闸
    limits.py            上限与冷却（3 次 / 90 分钟 / 10 分钟冷却 / 每日 6 次）
    picker.py            出题：当前单元 → 前两单元 → 其余，错题加权，≤2 题/知识点
    grade.py             判分：等价答案、⌈0.8n⌉ 通过线、幂等、上限在事务里扣
    ticket.py            凭证格式 AQ1.<payload>.<sig>，ES256/HS256 两种签名器
    es256.py             ECDSA P-256 + RFC 6979 + DER，标准库
    ids.py               ULID（schema 是 CHAR(26)）
    db.py                sqlite/mysql 适配 + 事务
  web/                   家长 H5，三个文件没有构建步骤（index.html / app.css / app.js + manifest + icon）
  tools/
    import_curriculum.py 知识点树 → 库（幂等，按自然键）
    migrate.py           老库补列补表（used_ms、config_audit），幂等
    gen_seed_questions.py 种子题：模板 × 参数，数学题过 sympy 才入库
    pdf_outline.py       教材 PDF → 目录文本（需 poppler / tesseract）
    genkey.py            生成 ES256 密钥对，打印 Kotlin 用的公钥常量
    interop_es256.py     与 openssl 四向交叉验签
    make_ca.sh           自签 CA + 服务器证书；CA 拷进 APK 的 res/raw/quiz_ca.pem
    write_tv_config.py   生成 local/quiz.properties（地址 + 公钥），供 Gradle 注入 BuildConfig
    make_tv_fixture.py   连上跑着的服务端真签一张票，存成电视端的测试向量
    check_schema_parity.py 两份 DDL 的列名比对
    check_tv_silence.py  电视端不许出现薄弱知识点 / 复习相关的任何字样或字段（WRONGBOOK §10）
    acceptance.py        端到端验收，158 条断言（P1 30 + P3 20 + 账号 53 + 掌握度与静默 29
                         + 三段配比与零回归 26）
```

## 跑起来

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r server/requirements.txt      # pymysql + sympy

python -m server.app.ticket                  # 凭证自检（HMAC + ES256 往返）
python -m server.app.es256                   # 曲线自检
python -m server.app.limits                  # 上限自检
python -m server.app.ids                     # ULID 自检
python -m server.app.mastery                 # 掌握度权重/档位/过关次数自检
python -m server.app.mix                     # 三段配比 S0–S3 与权重自检
python server/tools/interop_es256.py         # 与 openssl 交叉验签（需要 openssl）
python server/tools/check_schema_parity.py   # MySQL / SQLite 两份 DDL 不许漂移
python server/tools/check_tv_silence.py      # 电视端零表达（WRONGBOOK §10）

# 一条龙：建库(内存/文件) → 导树 → 造题 → 起服务 → 158 条验收
python server/tools/acceptance.py
```

手工跑一遍（看的见每一步）：

```bash
export QUIZ_DSN="sqlite:$PWD/server/local/dev.sqlite3"
python -m server.tools.import_curriculum
python -m server.tools.gen_seed_questions --per-topic 5
QUIZ_DEV=1 python -m server.app.api --port 8080
curl -s localhost:8080/health
```

家长页就在同一个进程的 `/`：`http://127.0.0.1:8080/`（电视要 https，见下节；手机走内网时用同一个
`--tls-cert` 起在 8443，页面地址是 `https://<局域网地址>:8443`）。第一次在手机上打开要把自签 CA 装一次。
```

`QUIZ_DEV=1` 允许请求体里带 `now_ms`，用来测「10 分钟过期」「跨天」这类事。**生产不开这个开关**，
`api.py` 里对它的判断是唯一的开关点。

**已经跑过一段时间的库要先补表**（2026-11-14 加了账号三张表和一个审计列）。`migrate.py` 只加不删，
跑两次也没事，MySQL 那边只打印语句、由人看过后执行：

```bash
python -m server.tools.migrate --dsn sqlite:server/local/tv1.sqlite3 --dry-run
python -m server.tools.migrate --dsn sqlite:server/local/tv1.sqlite3
```

补完之后，**已经配过对的手机第一次打开会停在「升级成账号」**：设一个邮箱密码，它绑着的那台电视自动
归到账号名下，电视不用重新出码（`/v1/auth/claim`，`docs/ACCOUNT.md` §3 A5）。

## 密钥

```bash
python -m server.tools.genkey --out server/local
```

输出一行 base64（X.509 SPKI）给 Kotlin：`Base64.decode` + `X509EncodedKeySpec` +
`Signature.getInstance("SHA256withECDSA")`，不加任何第三方库。私钥 `chmod 600`，另存离线备份——
**丢了它，手上还没兑换的票全部作废；泄露了它，任何票都能伪造。**

## TLS 与钉住的 CA（P2）

电视端不接受明文，也不信系统证书库——它只信 `app/src/main/res/raw/quiz_ca.pem` 一张证书，而那张
证书是 `make_ca.sh` 生成的 CA。整条链只有三个命令：

```bash
sh server/tools/make_ca.sh 203.0.113.9        # CA + 服务器证书；地址写电视能访问到的那个
python -m server.tools.write_tv_config --base-url https://203.0.113.9:8443
python -m server.app.api --host 0.0.0.0 --port 8443 \
    --tls-cert server/local/tls/server.pem --tls-key server/local/tls/server.key
```

三件容易踩的：

- **地址必须写进证书的 SAN。** 只写 CN 的证书被所有遵守 RFC 6125 的栈拒收，Android 5.0 起也一样。
- **换 CA 就要重装 APK。** 旧电视钉的是旧证书，症状是「服务器的证书不对」。
- **Python 3.12+ 要求叶子证书带 `authorityKeyIdentifier`。** `make_ca.sh` 已经加了；不加的话 curl 能过、
  Python 客户端过不去，很难查。

证书是 **RSA**，票是 **ECDSA**：前者要在 Android 5.0 到 14 的每一种 TLS 栈上都成立，后者是为了让 APK
里只放公钥。两件事不需要同一种算法。

## 已知限制

1. **电视那几条接口没有设备鉴权**。`device_id` 就在请求体里，知道别人的 id 就能替他交卷、烧他的次数。
   签不出票（票绑 `did`，电视会再查），但这仍是整个方案最大的洞：设备密钥 + 请求签名还没做。
   手机那一侧 2026-11-14 起已经有账号与会话（`/v1/auth/*`，`account_device` 决定能看哪台电视），
   漏的只剩电视自己发的那几条。
2. **限速分三处**，都是进程内计数、重启清零：配对 5 次/分、30 次/时、连错 3 次锁 10 分钟（`h5.Throttle`）；
   注册 5 小时 / 20 天每个来源 + 全站 60 次/时，登录 20 次/15 分钟，找回 20 次/15 分钟（`auth.Windows`）。
   在库里的锁有两条，重启不清零：账号连续 8 次密码错误锁 15 分钟（`account.fail_count`），
   找回答案连错 5 次锁 15 分钟（`account.recover_fail_count`）。
3. **邮箱不做验证**。没有邮件网关，所以邮箱只是登录名：发不出找回密码的信，也挡不住别人用你的邮箱注册。
   找回密码改走**自设的安全问题**（`POST /v1/auth/recover`，2026-11-15）。它是弱凭证：
   答案靠 5 次/15 分钟锁定兜底，熟人猜得中就是猜得中。详见 `docs/ACCOUNT.md` §3 A1 与 §11。
4. **SQLite 只是本机验收与家里那台用的**。`journal_mode=OFF`，单写者；生产是 MySQL。
5. **题库是种子**：86 题，模板生成，覆盖 18 个知识点。真实题库另行决定。
6. **不下发解析**。交卷后电视只能显示分数；解析只在家长 H5 的错题本里（`docs/QUIZ.md` §15）。

## 不入库的东西

`server/.env`、私钥（`*.pem`）、`server/local/`、`*.sqlite3`、`local/` 全部 gitignore。签名私钥只存在于 ECS 上。

## 部署：现在只做内网

A7 答「无域名 / 可开 443」。电视端不受影响（APK 内置自签 CA + `network_security_config`），
所以 P1/P2 用自签证书 + IP 直连可以。**手机页（P3）2026-11-12 定的是先内网**：手机连家里 Wi-Fi，
访问 `https://<局域网地址>:8443`，首次装一次同一张自签 CA，数据不出门（`docs/PARENTAL.md`
「家长 H5」）。真要走公网，手机浏览器对自签证书天天红警告，而公共 CA 基本不签裸 IP——
那时要解决的是域名 + 备案，不是代码：地址集中在 `local/quiz.properties` 与页面里的一处配置。

