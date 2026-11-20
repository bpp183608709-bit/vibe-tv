# 迁移演练（2026-11-20）：生产副本 → quiz_acceptance

> 对应 `docs/HANDOFF-上线与题库.md` §6.1 那条「下次部署捎带重导题库」。
> 结论先说：**流程能收敛，数字全对；但原流程有两处只有"库里已经有数据"才会撞的错，已修。**
> 演练没有写生产库，生产库只做了 `mysqldump`。

---

## 1. 演练的判据是什么

不是"跑完不报错"，而是六条同时成立：

| 判据 | 结果 |
|---|---|
| `unit` / `topic` / `question` = 28 / 67 / 1917 | ✅ 28 / 67 / 1917 |
| 每科题数与离线文件一致（数学 864、语文 576、英语 477） | ✅ |
| 每个 `(诊断节点, 能力线)` 格子正好 3 易 3 中 3 难 | ✅ 反例 0 行 |
| 迁移后的表结构与 `schema.sql` 新建库**逐表一致** | ✅ 列与键完全一致（仅索引书写顺序不同） |
| 残留 topic / 残留题清零，且删前引用计数为 0 | ✅ 7 个 topic、3 道题，`quiz_answer` 0 条 |
| 新代码在 MySQL 空库上端到端验收 | ✅ 256/256 |

---

## 2. 怎么跑（可复制）

```sh
# 0) 生产库备份（只读操作，不写 quiz）
mysqldump -uroot --single-transaction --routines --triggers quiz | gzip > /var/backups/wrong-quiz/drill-quiz-$(date +%Y%m%d-%H%M%S).sql.gz
gzip -t <上面那个文件>

# 1) 暂存新代码与数据（导入器按 <repo>/knowledge-eval/data 找文件，所以两个目录要一起）
tar czf - --exclude=__pycache__ server/app server/db server/tools/*.py server/tools/*.sh knowledge-eval/data \
  | ssh root@<host> 'mkdir -p /opt/wrong-quiz-drill && tar xzf - -C /opt/wrong-quiz-drill'
ln -sfn /opt/wrong-quiz/.venv /opt/wrong-quiz-drill/.venv
mkdir -p /opt/wrong-quiz-drill/server/local && chown wrongquiz /opt/wrong-quiz-drill/server/local

# 2) 用生产副本重建验收库，再跑 DDL
mysql -uroot -e "DROP DATABASE IF EXISTS quiz_acceptance; CREATE DATABASE quiz_acceptance CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci; GRANT ALL ON quiz_acceptance.* TO wrongquiz@localhost"
zcat <dump> | mysql -uroot quiz_acceptance
mysql -uroot quiz_acceptance < /opt/wrong-quiz-drill/server/db/migrate-2026-11-19-flat-nodes.sql
mysql -uroot quiz_acceptance < /opt/wrong-quiz-drill/server/db/migrate-2026-11-19-ability-mastery.sql

# 3) 导入（必须用 wrongquiz 身份，DSN 走 auth_socket）
su -s /bin/sh wrongquiz -c 'cd /opt/wrong-quiz-drill && \
  DSN="mysql://wrongquiz@localhost/quiz_acceptance?unix_socket=/run/mysqld/mysqld.sock"; \
  .venv/bin/python -u -m server.tools.import_curriculum --dsn "$DSN"; \
  .venv/bin/python -u -m server.tools.import_questions  --dsn "$DSN"'

# 4) 清理残留（先看计数再删，见 §6.1）
# 5) 验收查询（见 §6.1 末尾那段 SQL）
```

演练实际耗时：DDL 两秒内，两个导入器十几秒，其余是人工核对。

---

## 3. 演练前的生产库长什么样（这是"有数据"的定义）

| 项 | 值 |
|---|---|
| `unit` / `topic` / `question` | 27 / 53 / 39 |
| `quiz_answer` / `mastery_event` | **0 / 0** |
| `question.ability_tag` / `idx_question_pick` | 已存在（`flat-nodes` 的前置条件满足） |
| `unit.code` / `question.code` / `ability_mastery` / `weakness` | 都不存在（迁移要建） |
| 老题目 `question.code` | 迁移后仍是 NULL（`param_hash` 是哈希，回填不了） |

**0 条作答记录**这一点很关键：残留的 3 道题（`math-6up-d11` 按比分配）可以直接删，不必退而求其次改成 `retired_at`。

---

## 4. 演练发现的三个问题

### 4.1 `import_curriculum` 撞 `topic.uk_topic`（代码已修）

```
pymysql.err.IntegrityError: (1062, "Duplicate entry '17-1' for key 'topic.uk_topic'")
```

聚合节点的 `code` 是**位置**（`math-6up-u01`），而 2024 版课本把「确定位置」提到第 1 章、
「分数乘法」挪到第 2 章 —— 一次导入里 `u01`/`u02` 互换。诊断节点按 `code` 匹配到旧行后要搬去新的
`(unit_id, ordinal)`，那个位置还被另一条尚未搬走的老行占着。

空库跑一万次也看不到：空库没有旧行可撞。修法是先把本轮涉及的全部 `ordinal` 加 100 让位
（`TINYINT UNSIGNED` 装得下，同单元单射），循环再拿回 1..n。

### 4.2 `import_questions` 撞 `uk_question_dedup`（代码已修）

```
pymysql.err.IntegrityError: (1062, "Duplicate entry '31-hand-00411392d8184fd3' for key 'question.uk_question_dedup'")
```

`flat-nodes` 迁移给 `question` 加了 `code`，但**回填不了**。老库 39 道题 `code` 全是 NULL，
导入器只按 `code` 判"见过没有"，于是把每道老题当新题插，撞上 `(topic_id, template_key, param_hash)`。
现在 `code` 找不到时按去重键认领：保留原 id（作答记录不断），并采纳文件里的 `code`。

结果：36 道老题被原地认领，3 道（`math-6up-d11`）随节点一起删掉，`question.code IS NULL` 归零。

### 4.3 `acceptance_mysql.sh` 从来没真正跑绿过（夹具已修）

```
pymysql.err.OperationalError: (3140, 'Invalid JSON text: "The document is empty." ... weakness.evidence_question_ids')
```

验收夹具直接给 JSON 列写 `''`。SQLite 存 TEXT 不吭声，MySQL 的 JSON 列直接拒收。
改成 `'[]'` 之后 MySQL 空库验收 256/256 —— 这条路径以前是"文档里有、实际跑不通"。

---

## 5. 两处文档过期（比代码错更危险）

1. **残留 topic 不是"按 skipped 日志删"。** 旧版导入器在**每个单元处理时**就判残留，
   于是「只是从 u01 搬到 u02」的节点会被报成 `gone from the file`：真实迁移刷了 **14 行**，
   实际只有 **7 行**是真的。已改成"全部单元摆完之后仍未安置"才算残留，现在日志正好 7 行。
   但生产上如果用了旧版导入器，**只能按 §6.1 里写死的 7 个 code 删，不能照日志删**。
2. **`DELETE FROM unit WHERE code='math-6up-u09'` 这一步已经失效。** 新版数学的第 9 个条目是
   `math-6up-u-review`（复习与关联），导入时按 `(textbook_id, ordinal=9)` 兜底匹配，
   **接管了原来「综合与实践」那一行**（row id 不变，code 被改写成 `math-6up-u-review`）。
   所以 `math-6up-u09` 根本不存在，这条 DELETE 是空操作。
   要验的是 `SELECT name_zh FROM unit WHERE code='math-6up-u-review'` = 复习与关联。

另外一条部署提醒：导入器按 `<repo>/knowledge-eval/data` 找题库文件，而 `/opt/wrong-quiz` 下
**没有 `knowledge-eval/` 目录**。生产部署必须把 `knowledge-eval/data/*.json` 一起放上去，
只传 `server/` 会让两个导入器以 `FileNotFoundError` 或"导 0 题"收场。

---

## 6. 生产执行的修订清单

顺序与 §6.1 一致，只把上面几处改掉：

```sh
# 1) 备份并验 gzip
deploy/bin/backup.sh

# 2) 部署代码 + 题库文件（server/ 与 knowledge-eval/data/ 必须同时到位）
#    部署后核对 md5，尤其是 bin/app 与两个导入器

# 3) DDL（各一次）
mysql -uroot quiz < server/db/migrate-2026-11-19-flat-nodes.sql
mysql -uroot quiz < server/db/migrate-2026-11-19-ability-mastery.sql

# 4) 导入：预期输出
#    import: ok — 1 textbooks, 1 units, 21 topics, 7 skipped_topics
#    questions: ok — 1881 created, 36 updated, 6046 options, 390 answers
#    注意：created 数是"相对旧库"的，空库跑会是 1917 created

# 5) 清理残留（7 个 topic + 3 道题），删前计数必须全 0

# 6) 验收：28 / 67 / 1917 / 0 格反例
SELECT COUNT(*) FROM unit;      -- 28
SELECT COUNT(*) FROM topic;     -- 67
SELECT COUNT(*) FROM question;  -- 1917
SELECT COUNT(*) FROM ability_mastery;  -- 0（生产没有作答记录，不是错）
SELECT name_zh FROM unit WHERE code='english-6up-u09';  -- I don't feel well
SELECT name_zh FROM unit WHERE code='math-6up-u04';     -- 生活中的负数（就地改写，不是新建）
SELECT name_zh FROM unit WHERE code='math-6up-u-review';-- 复习与关联（接管了旧 u09 那一行）
-- 空格子反例应为 0 行：
SELECT t.code, q.ability_tag, SUM(q.difficulty=1), SUM(q.difficulty=2), SUM(q.difficulty=3)
  FROM question q JOIN topic t ON t.id=q.topic_id
 GROUP BY t.code, q.ability_tag
HAVING SUM(q.difficulty=1)<>3 OR SUM(q.difficulty=2)<>3 OR SUM(q.difficulty=3)<>3;

# 7) 重启并探活
systemctl restart wrong-quiz
sh /opt/wrong-quiz/bin/healthcheck.sh; echo $?
```

**回滚**：`quiz` 这份备份就是回滚点；DDL 全是加法（除 `question.unit_id` 改为可空、一次索引替换），
旧代码不会因为多出来的列而失败，所以"回滚 = 停服务 + 恢复备份"只在新数据不可接受时才需要。
