# 修复证据清单（2026-11-19）

> 范围：外部审查提出的 P0/P1/P2 全部条目，以及「仓库结构与入口」。
> 每一项都指向**实际改动**的文件与函数，并给出**可执行**的验证命令与结果。
> 本轮不接受「功能等价」「已列为遗留」作为完成理由；确实没做的写在最后第 8 节，并给出阻塞原因。

验证环境：macOS，Python 3.13.12，pytest 9.2.0；数据库用 `server/tools/acceptance.py` 生成的 sqlite 副本（生产 MySQL 未在本次变更中迁移，见 §8）。

---

## 0. 总览

| 要求 | 修复文件 | 关键位置 | 验证 | 结果 |
|---|---|---|---|---|
| P0 补回 `mt_g6u_036` | `knowledge-eval/data/nodes_math_6up.json` | 节点 `math-6up-u-review`（第 34 条）、`math-6up-d25`（第 35 条） | `check_content.py` 输出 `38/38` 且含 `mt_g6u_036` | PASS |
| P0 双向校验 | `knowledge-eval/scripts/check_content.py` | `check_coverage()` L101 | 两个负样本 + 换快照样本 | PASS |
| P1 节点数据 §4.1 直通 | `knowledge-eval/data/nodes_*.json`、`schemas/node.schema.json`、`scripts/check_schema.py` | `keval.validate()`；`check_schema.check_nodes()` L49 | `pytest tests/test_schema_direct.py` | PASS |
| P1 删除翻译层 | 删除 `server/tools/schema_check.py`、`server/tools/check_content.py` 旧实现 | 新 `check_content.py` 只接受原始 dict | `test_no_translation_layer_exists` | PASS |
| P1 聚合 `knowledge_type` 不再硬编码 | `data/nodes_*.json`（聚合无此字段） | 28 个聚合节点全部省略 | `check_content.py` 拒绝聚合带 `knowledge_type` | PASS |
| P1 `evaluate.py`（纯文件） | `knowledge-eval/scripts/evaluate.py` | `build_groups()`、`main()` L113 | 第 4 节命令 | PASS |
| P1 `export_from_db.py` | `knowledge-eval/scripts/export_from_db.py` | `export_nodes()` L55 / `export_questions()` L140 | `--check` 逐字段一致 | PASS |
| P1 报告默认只列薄弱 | `server/app/report.py` | `build(scope="weak")` L39 | `test_report_weak_only.py` | PASS |
| P1 `weakness` 落库 | `server/db/schema.sql` L544、`mastery._sync_weakness()` L292 | 同事务写入 | 验收「record three mistakes…」+ 报告读 `weakness` | PASS |
| P1 聚合稳定 ID | `unit.code`、`data/nodes_*.json` 的 `node_id` | `report_by_node()` 读 `u.code` | 连续两次导出 ID 一致（第 5 节） | PASS |
| P2 `question.unit_id` 空 | `schema.sql`、`import_questions.py`、`picker._candidates()` L93 | 无 `JOIN unit` | `check_db_parity --content` | PASS |
| P2 作答记录补字段 | `quiz_answer.diagnostic_node_id` / `ability_tag`、`grade.py` | 写入时带 `t.code`、`q.ability_tag` | `check_db_parity` + 验收 | PASS |
| P2 `ability_breakdown` 持久化 | `topic_mastery.ability_breakdown_json`、`mastery._ability_breakdown()` L269 | `_store()` 同步写 | 报告 `ability_breakdown` 非空 | PASS |
| P2 `options` 字符串 + `subject` | `data/questions_*.json`、`import_questions.py` | label = `A + 下标` | `check_schema.py` / `check_db_parity` | PASS |
| P2 `data_sources.md` 修正 | `knowledge-eval/docs/data_sources.md` §3 / §4 / §6 | U8/U9/总复习 独立单元；逐节点表；形态说明 | 人工核对 + 数据比对 | PASS |
| 入口 2 次点击 | 根 `README.md`「知识掌握评测」一节 | 指向 `knowledge-eval/README.md` 与证据文档 | 见 §7 | PASS |

---

## 1. P0：`mt_g6u_036` 补回

**改动**

- 新增聚合节点 `math-6up-u-review`（总复习，`chapter_order = 10`，`week_start = 20`）。
- 新增诊断节点 `math-6up-d25`「分数四则混合运算」，`parent_id = math-6up-u-review`，`source_ids = ["mt_g6u_036"]`，`prerequisite_node_ids = ["mt_g6u_034"]`。
- 新增 3 道题（`math-6up-d25-q01..q03`，中/易/难，覆盖「执行计算 / 情境应用 / 辨析变式」三种能力线），满足「至少 2 种 ability_tag + 至少 1 道中题」。
- `unit.code` 落库，`math-6up-u-review` 与其它章节一样有稳定 id。

**验证**

```bash
python knowledge-eval/scripts/check_content.py | grep -E "coverage|mt_g6u_036"
```
```
coverage pep-math-6up-v2.json: 38/38 micro points referenced
  mt_g6u_036  math-6up-d25     分数四则混合运算
```

**为什么不会再漏**：`check_coverage()` 反向比较两个集合，缺任何一个都会失败并点名（见下一节）。

---

## 2. P0：`check_content.py` 双向校验

**改动**（`knowledge-eval/scripts/check_content.py`）

- 正向：`referenced - snapshot` 非空 → 「nodes reference N micro point(s) the snapshot does not have: …」；
- 反向：`snapshot - referenced` 非空 → 「snapshot ... has 38 micro points, nodes reference 37; missing: …」；
- 两边数量都来自文件，代码里没有 `38`；
- 同一微观点被两个节点认领也报错（重复计数会让「覆盖」看起来比实际好）。

**验证（负样本，必须看到失败）**

```bash
python -m pytest knowledge-eval/tests/test_check_content_negative.py -v
```
```
test_pristine_data_passes PASSED
test_missing_source_id_fails PASSED
test_extra_source_id_fails PASSED
test_coverage_counts_come_from_the_files PASSED
```

第三个测试把快照砍到 10 条，证明比较逻辑随数据变化，不是硬编码通过。

---

## 3. P1：节点数据 §4.1 原样落地

**改动**

- `knowledge-eval/data/nodes_*.json` 的每个节点直接含：`node_id / name / subject / grade / semester / node_type / parent_id / chapter_name / knowledge_type（诊断可选）/ micro_nodes（字符串数组）/ prerequisite_node_ids / description / status / chapter_order`。
- 聚合节点**不带** `knowledge_type`：以前那个「数学一律 procedural、英语一律 reading」的硬编码已删除，28 个聚合节点一个都没有。
- 删除了 `flat_nodes()` 及其所在的旧 `check_content.py`、`schema_check.py`；`keval.validate()` 收到的是 `json.load` 的原始对象。

**验证**

```bash
python -m pytest knowledge-eval/tests/test_schema_direct.py -v
```
```
test_pristine_data_validates_directly PASSED
test_missing_node_type_fails PASSED
test_micro_nodes_as_objects_fails PASSED
test_aggregate_with_knowledge_type_fails PASSED
test_validator_receives_the_raw_objects PASSED      ← 校验器看到的对象 == 文件里的对象
test_no_translation_layer_exists PASSED             ← 脚本里搜不到 flat_nodes
```

---

## 4. P1：离线评估 `evaluate.py`

**改动**（`knowledge-eval/scripts/evaluate.py`）

- 只读文件：`--nodes` / `--questions` / `--answers` / `--out`；不 import 数据库，不连网。
- 窗口、加权、档位全部走 `server.app.mastery.summarise` / `level_of`（纯函数），与线上同一份实现。
- 输出前用 `schemas/report.schema.json` 自校验，不合格直接非零退出。

**验证**

```bash
python knowledge-eval/scripts/evaluate.py \
  --nodes knowledge-eval/data/nodes_math_6up.json \
  --questions knowledge-eval/data/questions_math_6up.json \
  --answers knowledge-eval/tests/fixtures/sample_answers_mixed_levels.json \
  --out /tmp/report.json
```
```
report: 10 chapters, 1 weak node(s), scope=weak -> /tmp/report.json
```

无 MySQL 的干净环境（只用标准库）即可跑通；`--out` 的 JSON 见 `docs/verification.md` 第 4 节。

---

## 5. P1：`export_from_db.py` 与聚合稳定 ID

**改动**

- 三张表投影成三类文件：`nodes_*`（§4.1）、`questions_*`（§4.2）、`answers_*`（§3.3）；不经过任何改名函数（`unit.code → node_id` 这类是列到字段的直接投影）。
- 聚合节点 id 一律取 `unit.code`；报告里 `aggregate_node_id` 读同一列，不再用 `f"{subject}-6up-u{ordinal}"` 现拼。

**验证**

```bash
python knowledge-eval/scripts/export_from_db.py --check
```
```
export: ok — 35 nodes, 22 nodes, 25 nodes; 18 questions, 12 questions, 12 questions (checked, not written)
```

连续两次导出的聚合 id 完全一致（`--check` 会逐节点比对，包含 `node_id`）；报告里的 `aggregate_node_id` 与数据文件的 `node_id` 同一来源。

---

## 6. P1：报告只列薄弱 + `weakness` 落库

**改动**

- `report.build(groups, period, scope="weak", source="db")`：`diagnostics` 只收 `level <= 1 且 sample >= 3`；`scope="all"` 才额外产出 `full_view`。
- `POST` 路径不变；`GET /v1/h5/mastery/report?scope=all` 才给全貌。
- 新增 `weakness` 表（`schema.sql` L544 / `schema.sqlite.sql` L424），`mastery._sync_weakness()` 在 `_store()` 同一事务里 upsert；level 回到 ≥2 时置 `resolved = 1`（保留行，见 `knowledge-eval/docs/data_sources.md` §6）。
- `h5.report_by_node()` 的薄弱行**从 `weakness` 读**（等级、正确率、样本、证据题目 id、父章节），不再现查现拼；`weak_list` 的成员判定也走 `weakness`。

**验证**

```bash
python -m pytest knowledge-eval/tests/test_report_weak_only.py -v
```
```
5 passed    ← 默认只有 L0；weak_count == 行数；L2/L3 只在 full_view；篡改报告会被 assert 脚本拒绝
```

端到端：`acceptance` 新增 4 条断言（默认只列薄弱 / 计数等于行数 / `?scope=all` 不动 diagnostics / full_view 是超集）。

---

## 7. P2：结构与文档

| 项 | 改动 | 验证 |
|---|---|---|
| `question.unit_id` | 列改 `NULL`；39→42 道题全部置空；`picker._candidates()` 去掉 `JOIN unit`，改用 `topic.chapter_order` | `check_db_parity --content`：`unit_id IS NOT NULL` = 0、`JOIN unit` = 0 |
| 作答记录 | `quiz_answer` 增 `diagnostic_node_id`、`ability_tag`，`grade.py` 写入 `topic.code` 与 `question.ability_tag`；历史行由迁移脚本回填 | 同上 + 验收判分用例 |
| `ability_breakdown` | `topic_mastery.ability_breakdown_json`，`mastery._store()` 每次重算并写；报告直接读该列 | 报告行内 `ability_breakdown` 非空（fixture 中可见三行能力线） |
| `options` / `subject` | 题库改字符串数组，label 按位置生成；每题带 `subject`；库里 `question.subject_id` 非空 | `check_schema.py`、`check_db_parity --content` |
| `data_sources.md` | 更正 U8/U9 独立；补逐节点映射；新增「形态说明」 | `knowledge-eval/docs/data_sources.md` §3、§4、§6 |
| 入口 | 根 `README.md` 新增「知识掌握评测」一节 → `knowledge-eval/README.md` → `data/`、证据文档 | 仓库首页两次点击可达 |

---

## 8. 本轮未做 / 有限制

1. **生产库未迁移**。本次只提交代码、数据、DDL 与迁移脚本；`47.95.198.148` 上的 MySQL 仍是旧结构。上线顺序必须是：备份 → 部署新代码 → 跑 `server/db/migrate-2026-11-19-flat-nodes.sql` → `import_curriculum` + `import_questions` → 重启。先迁移后部署会让旧进程读到 `unit_id IS NULL` 的题。
2. **`check_db_parity --content` 需要一个有数据的库**。没有库时它只能校验空表结构；本次用 `acceptance` 生成的 sqlite 副本（27 表 / 259 列）。
3. **语文/英语没有反向覆盖**。两科没有公开微观点分类法，`source_ids` 为空，反向集合为空集相等——这是数据现实，不是豁免：`micro_nodes` 仍要求至少 1 项，且题目必须落在存在的诊断节点上。
4. **`knowledge_type` 的「聚合节点不得有」是内容规则**，不在 JSON Schema 里：仓库的校验器实现了 draft-07 的一个子集，没有 `if/then`，写不出「诊断必填、聚合必无」的条件约束。因此它由 `check_content.check_nodes()` 拒绝，并有 `test_aggregate_with_knowledge_type_fails` 覆盖。
5. **`tags` 不落库**，`export_from_db.py --check` 跳过该字段（写作元数据，运行时不读）。

---

## 9. 复现全部结论

```bash
python knowledge-eval/scripts/check_schema.py
python knowledge-eval/scripts/check_content.py
python -m pytest knowledge-eval/tests/ -v
python knowledge-eval/scripts/evaluate.py --nodes knowledge-eval/data/nodes_math_6up.json \
  --questions knowledge-eval/data/questions_math_6up.json \
  --answers knowledge-eval/tests/fixtures/sample_answers_mixed_levels.json --out /tmp/report.json
python knowledge-eval/scripts/assert_report_weak_only.py /tmp/report.json
python -m server.tools.acceptance
```

期望：`schema: ok` / `content: ok` / `15 passed` / `1 weak node(s)` / `weak-only: ok` / `252/252 passed`。
