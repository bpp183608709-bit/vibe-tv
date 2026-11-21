# 题库密度审计：证据清单

对应报告 `reports/density-audit.md`。本文件只回答两件事：报告里的每个数字从哪来、怎么复现；审计脚本本身会不会判负。

审计日期：2026-11-21。数据：生产库 `quiz`（1917 题 / 67 节点）与 `knowledge-eval/data/*.json`（同一份题库的两处副本）。

## 一、复现命令

```sh
# 1) 正式报告（读题库文件，无需数据库）
python3 knowledge-eval/scripts/audit_question_density.py

# 2) 同一份审计改为读生产库（只读，一条 SELECT）
python3 knowledge-eval/scripts/audit_question_density.py \
  --dsn "mysql://root@localhost/quiz?unix_socket=/run/mysqld/mysqld.sock"

# 3) 落数据快照，供离线复现与负样本复用
python3 knowledge-eval/scripts/audit_question_density.py --emit-dataset /tmp/density-dataset.json

# 4) 负样本
pytest knowledge-eval/tests/test_density_audit.py -v
```

窗口不写死在脚本里：`configured_window()` 先 `from server.app.weakness import window_size`（`DEFAULT_WINDOW = 15`），取不到才回退 15。`--window` 可覆盖，L1/L2/L3 随之联动。

## 二、5.1—5.8 逐节来源

| 小节 | 数字 | 数据来源 | 生成位置 |
|---|---|---|---|
| 5.1 总览 | 1917 题 / 67 节点 / 8 个标签 / 3 档难度；理论格子 804、不设此线 591、声明格子 213、有题 213、空格子 0（0.0%） | `SUMMARY`：`questions`、`summary.*` | `build()` 的 `grid_summary` |
| 5.2 按学科 | 数学 864 题 25 节点 96 格（L3 25）；语文 576 / 22 / 64（L1 4、L3 18）；英语 477 / 20 / 53（L1 10、L3 10） | `subjects[]` | `build()` 的 `subject_rows` |
| 5.3 逐节点表 | 67 行 | `nodes[].questions / cells_declared / level / elimination.verdict / empty_lines` | `node_rows` |
| 5.4 矩阵 | 268 行（67 节点 × 学科 4 条线） | `nodes[].grid`（只有已声明线）+ `subjects[].ability_lines`（补齐 `—`） | `render_md()` 5.4 |
| 5.5 组合模拟 | 251 个组合 + 67 行结论（数学 138、语文 64、英语 49） | `nodes[].elimination.combos` | `elimination()` |
| 5.6 缺口 | P0 = 0、P1 = 14（语文 4、英语 10）、P2 = 0 | `gaps.p0/p1/p2` | `build()` 末尾 |
| 5.7 补题清单 | 补 28 道到 L2、98 道到 L3 | `patch[]`（每个 `(学科, 能力线, 难度)` 一行） | `allocate_patch()` + 聚合 |
| 5.8 结论 | L2+ 53（79.1%）、L1 14、L0 0、必重复 20.9%（28 道次） | `conclusion` | `build()` 末尾 |

全部数字由脚本写出，报告文件不含手填值。

## 三、两条数据源的一致性核对

同一份审计分别读题库文件与生产库，除 `meta.source` 外逐键比对：

| 键 | 结果 |
|---|---|
| `summary` | identical |
| `subjects` | identical |
| `nodes`（含 67 个节点的格子矩阵与组合模拟） | identical |
| `patch` | identical |
| `gaps` | identical |
| `conclusion` | identical |
| `meta.window` | 15（两个环境都从 `weakness.DEFAULT_WINDOW` 取到） |

核对结果：题库文件与生产库**逐题一致**，213 个格子无一例外。此前迁移时记录的「1917 题 / 213 格」由此再得一次独立印证。

服务器侧执行（只读）：

```sh
cd /tmp/density-audit && PYTHONPATH=/opt/wrong-quiz /opt/wrong-quiz/.venv/bin/python \
  knowledge-eval/scripts/audit_question_density.py \
  --dsn "mysql://root@localhost/quiz?unix_socket=/run/mysqld/mysqld.sock" \
  --out-json /tmp/density-audit/db.json --out-md /tmp/density-audit/reports/db.md
```

## 四、负样本结果

`pytest knowledge-eval/tests/test_density_audit.py -v` → **6 passed**。四条必需用例各自只破坏一个条件，另加两条不变量。

| # | 测试名 | 合成数据 | 断言 | 结果 |
|---|---|---|---|---|
| 1 | `test_thin_node_is_l0` | 单节点 4 条线，共 **10 题** | `level == "L0"`、进入 `gaps.p0`、必重复 5 道 | PASSED |
| 2 | `test_empty_declared_line_is_l0` | 单节点声明 3 条线、共 **18 题**，第三条线 0 题 | `empty_lines == ["情境应用"]`、`level == "L0"`、该行矩阵为 `0/0/0` | PASSED |
| 3 | `test_twenty_two_questions_with_one_medium_is_infeasible` | 4 条线共 **22 题**，全场只有 **1 道中题** | `level == "L2"` 但 `elimination.verdict == "infeasible"`、可行组合 0 | PASSED |
| 4 | `test_twenty_five_questions_evenly_spread_is_l3_and_strong` | 4 条线 12 格约 2 题、共 **25 题** | `level == "L3"`、`verdict == "strong"`、6/6 组合可行 | PASSED |
| 5 | `test_same_data_same_bytes` | 同数据跑两次 | JSON 与 MD **逐字节相同** | PASSED |
| 6 | `test_real_bank_parses` | 真实题库 | 能跑通、节点数自洽、档位取值合法 | PASSED |

用例 2 有一条反直觉但正确的断言，写在这里以免后人改错：**空能力线不会让消除模拟判负**。剩下两条线自己就有 3/3/3，任何组合都凑得出 5 道；空能力线卡住的是状态机的主因线识别（按线命名），不是取题。测试因此断言 `verdict == "strong"` 且同时断言 `level == "L0"`——两者并存，各说各的约束。

## 五、与既有门禁的口径差异

| | `check_ability_coverage.py`（门禁） | 本审计 |
|---|---|---|
| 判定单位 | 格子（节点 × 声明线） | 节点（整条闭环） |
| 阈值 | 每格 3/3/3 共 9 题 | 立案 15 + 消除 5 → L2=20、L3=25 |
| 结论 | 全绿（213 格满 3/3/3） | 53 个节点到 L3、14 个只到 L1 |
| 是否写文件 | 否 | 是（`reports/` 两份） |

两者都对，也不矛盾：格子整齐说明“每个点都能测到”，节点 18 题说明“测完之后没题可练”。任何一方单独看都会得出偏乐观的结论，这是同时保留两条口径的理由。

## 六、阈值与题量的结构性观察（只提出，不改动）

节点题量只取三个值：18 / 27 / 36，恰好是「能力线数 × 9」。因此 `L2 = 20–24` 这一段在数学上不可能有节点落入——报告 5.2 已如实标注该档为 0 且说明原因。

这不是缺陷，是两套刻度的错位：题库按“每格 3 易 3 中 3 难”写作，闭环按“15 + 5”消耗。若希望 L2 成为一个有意义的中间档，只有两条路——放宽单格题数（如允许某格 2 道），或把 L2/L3 重新定义为 18/27 对齐现有刻度。两条都属于教研决策，本轮只做审计，未改动任何阈值。

## 七、成本与影响

- 审计全程只读：`SELECT` 一条，未建表、未写入、未改题；`reports/` 之外无文件产出。
- 两条数据源分别耗时 < 1 秒；负样本 6 条合计 3 秒。
- 全量 `pytest` 由 158 条增至 **164 条，全部通过**。
