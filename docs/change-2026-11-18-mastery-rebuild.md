# 变更说明：六年级上册掌握度重构

> 变更日期：2026-11-18
> 提交范围：`626554e^..0fd4403`（5 个功能提交 + 1 个审查文档提交）
> 代码规模：41 个文件，+6336 / −952
> 线上环境：ECS `47.95.198.148`，`/opt/wrong-quiz`，服务 `wrong-quiz`（8443 H5 / 9443 TV / 8100 回环）

---

## 1. 一句话

把「错题加分制」换成「滚动窗口加权正确率」，并把题库从单层章节改成「聚合单元 + 诊断节点」两级树；
历史掌握度数据清空重算，旧数据已备份。

---

## 2. 提交清单

| 提交 | 内容 |
|---|---|
| `626554e` | content: 六年级上册三科诊断节点树与 39 道手写题 |
| `b510812` | mastery: 滚动窗口模型接入验收并全绿（242/242） |
| `284ae82` | report: 按章分组的报告端点与家长页 |
| `42cd1bb` | docs: 把数值规则、产品口径与数据来源改到滚动窗口模型 |
| `b7c158e` | db: 迁移脚本补上 `topic.code` 与 `knowledge_type` |
| `0fd4403` | docs: 掌握度重构落地情况审查（对照设计稿逐条比对） |

---

## 3. 掌握度模型（`server/app/mastery.py`）

**判定输入换成窗口**：每个 `(device, topic)` 只取最近 10 条作答，按难度加权，正确率 `acc_x1000` 上取整。

| 难度 | 权重 ×10 |
|---|---|
| easy | 10 |
| medium | 15 |
| hard | 20 |

`acc_x1000 = round(correct_weight × 1000 / weight_sum)`。

**档位（分数越高越好，方向与旧模型相反）**：

| 档 | 条件 | 文案 |
|---|---|---|
| 待测 | `sample < 3` | 待测 |
| L0 | `acc < 400` | 未掌握 |
| L1 | `400 ≤ acc < 600` | 基础 |
| L2 | `600 ≤ acc ≤ 800` | 熟练 |
| L3 | `acc > 800` 且 `hard_right ≥ 1` | 拓展 |

L3 要同时满足 `acc > 800` 和「至少答对 1 道 hard」，所以只刷中等题最多停在 L2：
十条中等题全对算出来是 `1000`，但 `hard_right = 0`，仍判 L2。反过来，中等题只对六成
（`600`）刚好压在 L2 下界上，一条错题就掉回 L1——0.6 这个系数是被严格边界卡出来的。

**只重算不累加**：每次作答后重算整窗，`--` 不再有「加分/减分」概念。以下事件 `counted=0`，不进窗口：
家长调档、连对奖励、错题作废、被标记的正确答案、当天第 4 道及以后的纸质错题。

**`need_right`** 改成纯 10 步模拟循环（原闭式算法在 `counted=0` 与难度分布不均时会算错），返回「还要连对几题才能进下一档」。

---

## 4. 题库与节点树（`server/content/`）

- `curriculum/grade6-up.json`：三科，27 个聚合单元 + 53 个诊断节点，先修边同期写入。

| 科目 | 版本 | 单元 | 诊断节点 |
|---|---|---|---|
| 数学 | 人教版六上 | 9（8 章 + 「综合与实践」，含「数学广角——数与形」） | 24 |
| 语文 | 统编版六上 | 8（第七、八单元按 D3 补入，设计稿只列了 6 个） | 14 |
| 英语 | 湘少版 2026 新版 | 10 | 15 |

  聚合单元只承担报告分组，不挂题；判定薄弱的只有诊断节点。
- `questions/*.json`：39 道题（32 选择 + 7 填空），128 个选项。语文、英语全为选择题——TV 遥控器只有
  `0-9 . / 退格 确定`（见 `QuizScreen.kt`），简答与判断无法作答。
- `taxonomy/pep-math-6up-v2.json`：人教版数学六上分类法快照，38 个微观点、55 条边，
  `fetched_at` 随快照记录。
- `schema/*.json`：node / question / report 三份 schema。

---

## 5. 数据库迁移（`server/db/migrate-2026-11-node-window.sql`）

全部是**加列**，不删不改，MySQL 8 无 `ADD COLUMN IF NOT EXISTS`，所以**只能跑一次**，跑前必须备份。

| 表 | 新增 |
|---|---|
| `topic` | `code`（唯一）、`micro_nodes_json`、`prereq_json`、`knowledge_type` |
| `question` | `ability_tag` |
| `daily_state` | `mix_deepest` |
| `topic_mastery` | `sample_count`、`weight_sum_x10`、`correct_weight_x10`、`acc_x1000`、`hard_right`、`seq`、`bias`、`weak_since` |
| `mastery_event` | `is_correct`、`difficulty`、`weight_x10`、`counted`、`acc_after`、`sample_after`、`seq` + 索引 `idx_event_window(device_id, topic_id, seq)` |

迁移后 `SHOW CREATE TABLE` 与全新 `schema.sql` 逐列一致：**26 表 / 237 列**。

---

## 6. 接口与家长页

**新增端点** `GET /v1/h5/mastery/report`：按聚合单元分组，组内列诊断节点，含
`level_label`、`weighted_accuracy`、`sample_count`、`hard_right`、`bias`、`need_sample`、`need_right`。
样本 < 3 显示「待测」。原 `/v1/h5/report`（近 7 天周报）未动。

**前端** `server/web/app.js` / `app.css`：
- 薄弱页由「平铺知识点列表」改为「章节卡片 → 章内知识点」，卡片头显示 `{n} 个知识点 · {m} 个薄弱`。
- 档位色阶反向：L3 绿、L2 蓝、L1 黄、L0 红（`.mbar.l3`、`.pill.l3` 同步，深色模式一并改）。
- 详情页增加能力线说明与建议语。

---

## 7. 上线过程

1. **备份**：`/var/backups/wrong-quiz/quiz-before-reset-20261118-203445.sql.gz`（26 表，`gzip -t` 通过），
   本地另存一份于 `local/backup/`；代码备份 `/opt/wrong-quiz/server.bak-20261118-203518`。
2. **演练**：迁移在临时库 `quiz_migtest` 上跑一遍，与 `schema.sql` 新建的 `quiz_schematest` 逐列比对，
   结果 **IDENTICAL**。
3. **迁移**：线上应用，得到 237 列。
4. **清库重导**：`reset_content.py` 清 370 行业务数据，重导 3 册 / 27 单元 / 53 节点 / 39 题 / 128 选项。
   清库与导入以 `runuser -u wrongquiz` 执行（MySQL 账号是 `auth_socket`，root 直连会被拒）。
5. **重启**：三端口监听正常，`healthz` 200。

**验证**：`pick_group` 在 S0 出新客混合 5 题、覆盖三科；`report_by_node` 返回 27 章 / 0 诊断（全部「待测」，
符合新库预期）；`weak_list` 返回 0 且文案正确。

---

## 8. 回滚

```sh
systemctl stop wrong-quiz
rm -rf /opt/wrong-quiz/server && cp -a /opt/wrong-quiz/server.bak-20261118-203518 /opt/wrong-quiz/server
zcat /var/backups/wrong-quiz/quiz-before-reset-20261118-203445.sql.gz | mysql quiz
systemctl start wrong-quiz
```

迁移只加列，回滚到旧代码时新列仍存在但无人读取，不影响旧逻辑。

---

## 9. 验收基线

| 检查 | 结果 |
|---|---|
| `server/tools/acceptance.py` | 248 / 248 |
| `server/tools/check_content.py` | ok |
| `server/tools/check_schema_parity.py` | 26 表 / 237 列，两边一致 |

验收里掌握度相关断言全部换成手算期望值（例如中等题 2 对 3 错 → `33 / 50 / 60`；
简单对 vs 困难错 → `333`；困难对 vs 简单错 → `667`），不再由实现反推。

---

## 10. 遗留项

对照设计稿的偏差已单列一份审查：`docs/audit-2026-11-18-mastery-rebuild.md`。

其中只有 1 个实质缺陷：`mt_g6u_036`「分数四则混合运算」（centrality 0.8，是 `mt_g6u_034` 的先修项）
未被任何节点引用，38 个微观点实际只覆盖 37 个。根因是归并脚本按章节分组、该点 `standards` 落在「总复习」，
而校验器只做「引用→存在」单向检查，缺「快照→覆盖」反向检查。
