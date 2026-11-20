# 生产库迁移执行记录（2026-11-21 01:07–01:15 CST）

> 对应 `docs/HANDOFF-上线与题库.md` §6.1 的「下次部署捎带重导题库」，按 `docs/migration-drill-2026-11-20.md`
> §6 修订清单执行。**这次真的写了生产库。**

---

## 1. 结果

| 项 | 迁移前 | 迁移后 |
|---|---|---|
| `unit` | 27 | **28** |
| `topic` | 53 | **67** |
| `question` | 39 | **1917** |
| 每科题数 | 数学 27 / 语文 3 / 英语 9 | **数学 864 / 语文 576 / 英语 477** |
| 每格 3 易 3 中 3 难 反例 | — | **0 行** |
| 库结构 vs `schema.sql` | 缺 `unit.code`、`question.code`、`ability_mastery`、`weakness` | **列与索引完全一致** |
| `quiz_answer` / `quiz_session` / `mastery_event` | 0 / 0 / 0 | 0 / 0 / 0 |

`question.code IS NULL` = 0，`question.code` 重复 = 0，没有题目的 topic = 0，`unit_id` 残留 = 0。
服务 `active`，三监听（443 / 9443 / 8100），`healthcheck.sh` 返回 0，`/readyz` db+llm 均 ok，`/v1/auth/me` 401。

---

## 2. 步骤与实测输出

```sh
# 0) 停服（01:07）。新代码依赖新表，先停再换，避免请求打在缺表的库上
systemctl stop wrong-quiz

# 1) 备份（01:07:06）——这是回滚点
/opt/wrong-quiz/bin/backup.sh
#   ok /var/backups/wrong-quiz/quiz-20261121-010706.sql.gz 81853 bytes, 6 kept
#   gzip -t 通过
# 代码快照：/var/backups/wrong-quiz/code-20261121-0107xx/pre-migrate-code.tar.gz（547 KB）

# 2) 部署（rsync，必须带 -e 'ssh -i ...'，否则卡在密码提示）
rsync -az --exclude __pycache__ server/app/    :/opt/wrong-quiz/server/app/
rsync -az --exclude __pycache__ server/db/     :/opt/wrong-quiz/server/db/
rsync -az --exclude __pycache__ server/tools/  :/opt/wrong-quiz/server/tools/
rsync -az knowledge-eval/data/                 :/opt/wrong-quiz/knowledge-eval/data/
#   8 个关键文件 md5 与本地逐一对齐（api/grade/h5/mastery/picker/report + 两个导入器）
#   server/web 三个文件本来就一致，未动；server/local 未动

# 3) DDL（各一次，均成功）
mysql -uroot quiz < server/db/migrate-2026-11-19-flat-nodes.sql
mysql -uroot quiz < server/db/migrate-2026-11-19-ability-mastery.sql

# 4) 导入（wrongquiz 身份，DSN 走 auth_socket）
import: ok — 1 textbooks, 1 units, 21 topics, 7 skipped_topics
questions: ok — 1881 created, 36 updated, 6046 options, 390 answers
#   三科：math 852+12 / chinese 564+12 / english 465+12

# 5) 清理残留：7 个 topic + 3 道题（全部引用计数 0 才删）
#   28 / 74 / 1920 → 28 / 67 / 1917

# 6) 启动 + 探活
systemctl start wrong-quiz
```

`schema.sql` 落在临时库 `quiz_schema_probe` 上比对过：`information_schema.columns` 与
`information_schema.statistics` 两个维度 `diff` 均为空，比对完即删库。

---

## 3. 作答数据的处理

**清理范围（显式 DELETE，删前后都计数）**：`quiz_answer`、`quiz_session`、`mastery_event`、
`ability_mastery`、`weakness`、`topic_mastery`、`topic_signal`、`wrong_item`、`paper`、`ticket`。

实测删前删后全部为 0：2026-11-18 那次重置已经把作答清过，之后孩子只看了电视没做题。所以这一步
是"确认没有残留"，不是"真的删掉了什么"。`question_answer`（答案键，390 行）不在清理范围，属于题库。

**保留未动**：`account`(1) / `device`(1) / `account_session`(3) —— 电视还绑着，删了要重新配对；
`curriculum_progress`(3) —— 三科都停在第 1 单元，是日历推出来的，不是作答结果；
`daily_state`(4) —— 每天额度与观看时长，是家长周报的数据源，不属于作答记录。
若也要清空这两张表，单独说一声，一条 DELETE 的事。

---

## 4. 冒烟：真的出一次题

只跑探活不算验完，把迁移后的库整份复制到 `quiz_smoke`，用生产代码调 `picker.pick_group`：

```
device 01M7KVZR0Q8XKED831RD5N3YC6
difficulty 1: 5 questions, subjects=[1, 2, 3]
difficulty 2: 5 questions, subjects=[1, 2, 3]
difficulty 3: 5 questions, subjects=[1, 2, 3]
```

三个难度都能出满 5 题、覆盖三科，没有 `NotEnoughQuestions`。跑完 `quiz_smoke` 已删。

---

## 5. 教训（下次别再踩）

1. **rsync 不读 `-i` 的密钥**，必须 `-e 'ssh -i /path/key'`，否则交互式要密码、在非交互环境里直接被杀。
2. **`knowledge-eval/data` 目录要先 `mkdir -p`**，rsync 不会替你建多级父目录（这里只建一级）。
3. 停服窗口 01:07–01:11，共 4 分钟，包含 DDL、导入、清理、建临时库比对。夜里做是对的。

---

## 6. 回滚

```sh
systemctl stop wrong-quiz
zcat /var/backups/wrong-quiz/quiz-20261121-010706.sql.gz | mysql -uroot quiz
tar xzf /var/backups/wrong-quiz/code-<时间戳>/pre-migrate-code.tar.gz -C /opt
systemctl start wrong-quiz
```

DDL 全是加法（除 `question.unit_id` 改可空、一次索引替换），旧代码不会因为多出来的列而失败，
所以"回滚"只在题数据本身不可接受时才需要。
