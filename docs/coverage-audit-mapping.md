# 题库密度审计：字段映射与取值体系

本文件是 `scripts/audit_question_density.py` 的输入约定。审计本身只读数据：不写库、不改题、不动 L0–L3 阈值。

## 一、中性概念 → 工程字段

| 任务文档概念 | 工程实际字段 | 位置 | 说明 |
|---|---|---|---|
| 题目 ID | `question.code` | `question` | 业务键 `math-6up-d01-q01`，题库文件与作答记录都用它；自增 `id` 是实现细节，不进报告 |
| 所属诊断节点 | `question.topic_id` → `topic.code` | `question` / `topic` | `topic` 即诊断节点（`topic.code` 如 `math-6up-d01`） |
| 能力标签 | `question.ability_tag` | `question` | `VARCHAR(16)`，中文四值，见第三节 |
| 难度 | `question.difficulty` | `question` | `TINYINT` 1/2/3 = 易/中/难 |
| 题型 | `question.qtype` | `question` | `choice` / `fill`；本轮审计不用它分档，仅在 JSON 里带出 |
| 启用状态 | `question.retired_at IS NULL` 且 `question.verify_status <> 'rejected'` | `question` | 生产库现为 1917 题、`retired_at` 非空 0 条、`rejected` 0 条，见第四节 |
| 诊断节点 ID | `topic.id` / `topic.code` | `topic` | 一个 `topic` 行 = 一个诊断节点 |
| 上级聚合节点 | `topic.unit_id` → `unit.id` / `unit.code` / `unit.name_zh` | `topic` / `unit` | 章节，只做分组，不参与判定 |
| 节点名称 | `topic.name_zh` | `topic` | 报告里给人读的那一列 |
| 章节顺序 | `topic.chapter_order` | `topic` | 报告排序用 |
| 学科 | `unit.textbook_id` → `textbook.subject_id` → `subject.name_zh` | `unit` / `textbook` / `subject` | `topic` 自身不带学科，必须经 `unit` 两跳到 `subject` |

没有重命名任何字段，没有新建平行表。审计全程只做 `SELECT`。

### 设计侧数据：每节点“该设哪几条能力线”

数据库不存这个信息，它存在题库源文件的节点声明里：

| 文件 | 用途 |
|---|---|
| `knowledge-eval/data/nodes_math_6up.json` | 节点树；`node_type == "diagnostic"` 的行带 `ability_lines` |
| `knowledge-eval/data/nodes_chinese_6up.json` | 同上 |
| `knowledge-eval/data/nodes_english_6up.json` | 同上 |

`ability_lines` 是教研对“这个节点该考哪几条线”的声明，也是 `check_ability_coverage.py` 用来判“格子是否完整”的单位。审计把它当作“设计上设不设此线”的唯一依据：

- 声明了且题量 > 0 → 格子有题；
- 声明了但题量 = 0 → 矩阵里写 `0`，并计入“空能力线”，节点直接判 L0；
- 未声明 → 矩阵里写 `—`，语义是“该节点不设此线”，不计入缺口。

## 二、难度取值体系

`question.difficulty` 为 1/2/3，无其他取值。题库源文件写的是中文 `difficulty_preset`，映射固定：

| 源文件 | 数据库 | 本报告用词 |
|---|---|---|
| `易` | `1` | 易 |
| `中` | `2` | 中 |
| `难` | `3` | 难 |

生产库分布：数学 288/288/288，语文 192/192/192，英语 159/159/159——三科各自三档严格等量。

## 三、能力标签全集

按学科取“设计上的全部能力线”，不以题库里出现过的标签为准：

| 学科 | 设计能力线（4 条） |
|---|---|
| 数学 | 理解意义 / 执行计算 / 情境应用 / 辨析变式 |
| 语文 | 语言运用 / 阅读理解 / 辨析判断 / 综合表达 |
| 英语 | 语言运用 / 阅读理解 / 辨析判断 / 综合表达 |

与任务文档第二节的描述完全一致，无差异。

但**每条线是节点级声明，不是学科强制**。工程的实际形态：

| 学科 | 节点数 | 4 线 | 3 线 | 2 线 | 声明格子数 |
|---|---|---|---|---|---|
| 数学 | 25 | 21 | 4 | 0 | 96 |
| 语文 | 22 | 2 | 16 | 4 | 64 |
| 英语 | 20 | 3 | 7 | 10 | 53 |
| 合计 | 67 | 26 | 27 | 14 | 213 |

依据（`ability_lines` 原文）：

- **数学 4 个三线节点** `math-6up-d05`（用方向和距离确定位置）、`d26`（用有序数对确定位置）、`d27`（负数的意义与读写）、`d13`（圆的认识与对称性）——位置与图形认识类节点不含“执行计算”，声明里就没有这条线。
- **语文 4 个两线节点** `chinese-6up-d22`、`d12`、`d14`（阅读理解 + 辨析判断）、`d20`（语言运用 + 辨析判断）——文本理解类节点不设“语言运用/综合表达”。
- **英语 10 个两线节点**：`d04`、`d06`、`d09`、`d10`、`d11`、`d13`（语言运用 + 辨析判断），`d18`、`d19`、`d20`、`d21`（阅读理解 + 辨析判断）——语法点与阅读短文类节点不设“综合表达”。
- **“综合表达”全库只挂在 5 个节点上**：语文 `d04`、`d08`（2 个），英语 `d02`、`d16`、`d17`（3 个），合计 5 个格子 45 题（18 + 27）。

这条声明与数据库实际标签**逐节点一致**：67 个节点里没有一个“声明了却没有题”的线，也没有“有题但未声明”的线。这一致性本身就是一条审计结论（见报告 5.1）。

## 四、启用状态

工程有状态字段，因此不需要“全部视为启用”的兜底：

| 字段 | 取值 | 生产库现状 |
|---|---|---|
| `question.retired_at` | `NULL` = 在库 | 1917 题全部 `NULL` |
| `question.verify_status` | `draft`/`sympy_ok`/`manual_ok`/`rejected` | `rejected` 0 条 |

审计口径：`retired_at IS NULL AND verify_status <> 'rejected'` 才算 1 道题。当前 1917 道全部计入。

## 五、立案窗口与消除短窗的实际配置

| 参数 | 值 | 来源 |
|---|---|---|
| 立案窗口 `window` | **15** | `server/app/weakness.py` → `DEFAULT_WINDOW = 15`，运行时可被 `QUIZ_WEAKNESS_WINDOW` 覆盖 |
| 消除短窗 `short` | **5**（2 中 + 1 难 + 2 易/中） | 本轮任务规格第三节；工程尚未实现强化包，故无对应常量 |
| 一轮闭环 | 15 + 5 = **20** | 由上两行推出 |
| 两轮闭环 | 20 + 5 = **25** | 复发后再次消除 |

脚本启动时先尝试 `from server.app.weakness import window_size`，取到就用工程配置，取不到回退 15；`--window` 可显式覆盖。因此阈值与线上代码同源，不会出现“报告按 15 算、代码按别的算”。

## 六、与任务文档示例的两处偏差（如实记录）

1. **不存在“10 题节点”这类极端薄节点**：当前最小节点有 18 题（2 条线 × 9），所以真实数据里 L0 为 0 个。任务文档 P0 示例中的 `math-6up-d03` 实为 36 题四线节点。报告不套用示例数字，全部由脚本算。
2. **“空能力线”在真实数据里为 0 条**：声明与题库逐节点一致，所以 213 个声明格子全部有题。但脚本仍保留这条判定与对应负样本（测试 2），否则将来某批题被删就会漏判。

## 七、审计单元与计算口径

- 格子 `(D, A, F)`：`count = COUNT(启用题目 WHERE topic=D AND ability_tag=A AND difficulty=F)`。
- `count_node(D)`：该节点全部启用题量。
- 理论格子总数 = 67 节点 × 4 线 × 3 难度 = 804；其中 591 格为 `—`（该节点不设此线）。
- 四档判定：

```
L0  题量 < 15  或  任一已声明线题量为 0
L1  15 ≤ 题量 < 20
L2  20 ≤ 题量 < 25
L3  题量 ≥ 25
```

- 消除可取性：对节点全部已声明线的两两组合（单线节点取自身），算
  `medium = Σ中`、`hard = Σ难`、`easy = Σ易`，
  可凑出 ⇔ `medium ≥ 2 且 hard ≥ 1 且 (medium+hard+easy) ≥ 5`。
  全组合可凑 → 强可行；部分 → 条件可行；全部不可 → 不可行。

## 八、报告与脚本的落点

| 交付物 | 路径 |
|---|---|
| 审计脚本 | `knowledge-eval/scripts/audit_question_density.py` |
| 机器可读结果 | `reports/density-audit.json` |
| 人读报告 | `reports/density-audit.md` |
| 负样本测试 | `knowledge-eval/tests/test_density_audit.py` |
| 证据清单 | `docs/coverage-audit-evidence.md` |

脚本放在 `knowledge-eval/scripts/` 而非仓库根的 `scripts/`：既有审计脚本（`option_audit.py`、`check_*.py`）都在这里，共用 `knowledge-eval/data` 的相对路径。
