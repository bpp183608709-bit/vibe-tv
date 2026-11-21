# 薄弱项计算：字段映射与现状盘点（2026-11-21）

> 本文件是「按加权正确率规则重做薄弱项计算与状态机」的第一步：先说清工程里已有什么、
> 文档里的中性概念落到哪个字段、哪些字段要新增。**这一步不改任何代码。**
>
> 第二份文档是 [`weakness-calc-evidence.md`](weakness-calc-evidence.md)（测试证据与新旧差异）。

---

## 1. 现有相关表（只列与计算有关的列）

### 1.1 主体与树

| 表 | 关键列 | 说明 |
|---|---|---|
| `device` | `id` CHAR(26) | **学生轴**：一台电视 = 一个孩子。全库的 `device_id` 都指它 |
| `unit` | `id`、`textbook_id`、`ordinal`、`code`、`name_zh` | 聚合节点（章节）。`code` 形如 `math-6up-u01` |
| `topic` | `id`、`unit_id`、`ordinal`、`chapter_order`、`code`、`name_zh` | **诊断节点**。`code` 形如 `math-6up-d01` |

### 1.2 题目

| 列 | 类型 | 说明 |
|---|---|---|
| `question.id` | INT UNSIGNED | 自增主键，不对外 |
| `question.code` | VARCHAR(40) | 稳定业务键 `math-6up-d01-q03`，导入后无 NULL、无重复（已核） |
| `question.topic_id` | INT UNSIGNED | 所属诊断节点 |
| `question.ability_tag` | VARCHAR(16) | 能力标签，中文，见 §3 |
| `question.difficulty` | TINYINT | **1 易 / 2 中 / 3 难**，全库 639/639/639 |
| `question.retired_at` | DATETIME NULL | 已下架题目不再出，但历史作答仍在 |

### 1.3 作答与派生

| 表 | 关键列 | 说明 |
|---|---|---|
| `mastery_event` | `id`、`device_id`、`topic_id`、`question_id`、`ability_tag`、`kind`、`is_correct`、`difficulty`、`weight_x10`、`counted`、`seq`、`ref_id`、`created_at` | **作答账本**，也是两份窗口的唯一输入 |
| `topic_mastery` | `score`、`level`、`acc_x1000`、`sample_count`、`hard_right`、`bias`、`weak_since`… | 节点快照，当前由「窗口 10 条、按次计数」算出 |
| `ability_mastery` | `ability_tag`、`level`、`acc_x1000`、`sample_count`、`hard_right`… | 能力线格子，各自独立 10 条窗口 |
| `weakness` | `weakness_id`、`device_id`、`topic_id`、`diagnostic_node_id`、`mastery_level`、`weighted_accuracy`、`sample_count`、`evidence_question_ids`、`parent_aggregate_node_id/name`、`resolved`、`opened_at`、`updated_at` | 薄弱项实体，一个（设备,节点）最多一行 |

`mastery_event` 的取值事实：

- `kind ∈ {tv_wrong, tv_right, paper_wrong, streak_bonus, parent_adjust, bad_question_void}`；
- `is_correct` 为 NULL 的行不是作答（`parent_adjust`、`bad_question_void`）；
- `difficulty` 为 NULL 的是试卷错题（无难度、无机判）；
- `counted=0` 有两种含义：**试卷当日第 4 道起**（仍算作答）和**被退题**（不算证据）；
- `seq` 是该节点内的单调序号，一次交卷 5 题连续递增；`created_at` 只到秒，同批同秒，
  所以**顺序一律按 `seq` 倒序**，不用 `created_at`。

---

## 2. 概念 → 字段映射

| 文档概念 | 工程字段 | 备注 |
|---|---|---|
| 学生 | `device.id` / `device_id` | 不引入 `student_id`；报告里的 `student_id` 字段当前就填 `device_id` |
| 诊断节点 | `topic.id`（外键）＋ `topic.code`（对外） | `weakness.diagnostic_node_id` 存 `code`，与现有写法一致 |
| 上级聚合节点 | `unit.code` / `unit.name_zh` | 即报告里的「章节」 |
| 题目 ID | `question.id` | 窗口去重用；对外展示用 `question.code` |
| 作答记录 | `mastery_event` 的一行 | 见 §1.3 |
| 是否正确 | `mastery_event.is_correct` | 1/0，NULL 表示非作答 |
| 难度 | `mastery_event.difficulty`，回落到 `question.difficulty` | 1/2/3 → 权重 1.0/1.5/2.0 |
| 能力标签 | `mastery_event.ability_tag`（写入时从 `question` 抄一份） | 中文 8 个取值，见 §3 |
| 作答时间 | `mastery_event.created_at`，同秒用 `seq` 兜底 | 排序键：`seq DESC` |
| 加权正确率 WA | `acc_x1000 / 1000` | 千分整数，避免浮点比较 |
| 等级 | 新增函数返回 0–3 | **不复用 `mastery.level_of`**，边界不同，见 §5.2 |
| 薄弱项状态 | `weakness.resolved` | 0 = active，1 = resolved；无记录 = 未立案 |
| 立案时间 | `weakness.opened_at` | 复发不清零 |
| 证据题 | `weakness.evidence_question_ids` | 现有列，JSON 数组，存 `question.code` |

---

## 3. 能力标签与难度体系（工程现状，不重新定义）

**能力标签**（`question.ability_tag`，VARCHAR(16)，全库 1917 题无 NULL）：

| 科目 | 标签 | 题数 |
|---|---|---|
| 数学 | 理解意义 / 辨析变式 / 情境应用 / 执行计算 | 225 / 225 / 225 / 189 |
| 语文 | 辨析判断 / 阅读理解 / 语言运用 / 综合表达 | 198 / 189 / 171 / 18 |
| 英语 | 辨析判断 / 语言运用 / 阅读理解 / 综合表达 | 180 / 144 / 126 / 27 |

单个诊断节点带 2–4 条能力线（`COUNT(DISTINCT ability_tag)` 最小 2、最大 4）。
「主因线」因此最多 4 个候选，取样本 ≥2 的前 2 个。

**难度**（`question.difficulty`）：`1` 易、`2` 中、`3` 难，全库严格 639/639/639。
试卷错题 `difficulty IS NULL`，沿用现有口径按「易」计（`PAPER_WEIGHT_X10 = 10`）。

---

## 4. 缺口与新增字段

**优先复用**：`resolved` 当状态位；`mastery_level` 当 `current_level`；`weighted_accuracy` /
`sample_count` 当当前窗口的 WA 与 n；`evidence_question_ids` 当 `detection_evidence_ids`；
`opened_at` 当首次立案时间；`parent_aggregate_*` 继续存章节。

**必须新增**（全部加在现有 `weakness` 表上，不新建平行表）：

| 新列 | 类型 | 用途 |
|---|---|---|
| `initial_level` | TINYINT NULL | 立案时的等级（0/1），冻结 |
| `detection_snapshot` | JSON NULL | 立案时的 WA、n、难度分布、medium/hard 答对数 |
| `detection_history` | JSON NULL | 历次复发时的旧立案快照（数组） |
| `current_window_snapshot` | JSON NULL | 当前窗口的 WA、n、medium_correct、hard_correct |
| `closing_snapshot` | JSON NULL | 关闭时的四项指标 |
| `cause_tags` | JSON NULL | 主因线（最多 2 条，含各自 WA/等级/难题答错数） |
| `cause_status` | VARCHAR(24) NULL | `ok` / `insufficient_sample` |
| `resolved_at` | DATETIME NULL | 关闭时间 |
| `reopen_count` | TINYINT UNSIGNED DEFAULT 0 | 复发次数 |
| `reopened_at` | DATETIME NULL | 最近一次复发时间 |
| `last_practiced_at` | DATETIME NULL | 最近一次「有新作答进入窗口」的时间；**只有做题才动** |

窗口大小 `window_size = 15` 不入库，作为模块常量，允许环境变量 `QUIZ_WEAKNESS_WINDOW` 覆盖。

---

## 5. 与现有实现必须说清的四处差异

### 5.1 窗口口径：10 条按次 vs 15 条按题去重

现有 `mastery.MASTERY_WINDOW = 10`，一条作答算一条，同一道题做五次就是五条。新规则是
**15 条、同一题只留最近一次**。两者不能共用一套常量，否则会连带改掉出题档位、配比、
「再答对 N 次」和暂停阈值。

**处理**：新模块 `server/app/weakness.py` 自己建窗口（`WEAKNESS_WINDOW = 15`），
`mastery.py` 的 10 条窗口与出题逻辑**本轮不动**。代价是同一个节点会有两个「等级」：
`topic_mastery.level`（出题用，10 条）与 `weakness.mastery_level`（薄弱项用，15 条去重）。
两者只在样本刚过 3 条、或窗口内容被重复题影响时不同。报告轮统一口径时再合并。

### 5.2 0.8 边界：旧实现判 L2，新规则判「3 级候选」

`mastery.level_of` 写的是 `if acc <= BAND_EXTENDED: return 2`，所以 WA 恰好 0.800 是 L2。
新规则要求 `0.8 归 3 级候选，再受 hard_correct 约束`。**新函数用 `>= 800`**，
旧函数不改——改它会连带影响 L3 的出题难度提升逻辑。

### 5.3 关闭条件：旧实现「升到 L2 就关」，新规则要四条同时成立

旧 `_sync_weakness`：`level > 1` 就置 `resolved = 1`，即 WA 到 0.6 就关闭。
新规则：`WA ≥ 0.8` 且 `hard_correct ≥ 1` 且 `medium_correct ≥ 3` 才关闭。
这意味着 **0.6–0.8 之间的节点会继续保持 active**，薄弱项会比现在留得更久——这是规则的
预期变化，不是 bug。验收里那条「十道答对就 resolved」的用例要按新阈值重写。

### 5.4 `counted=0` 一列两义

退题与「试卷当日第 4 道起」共用 `counted=0`。新窗口按 `counted=1 AND is_correct IS NOT NULL`
取数，于是两者都被排除——退题排除是对的，试卷溢出行被排除也是对的（它本来就不该进窗口）。

### 5.5 试卷错题没有 `question_id`

`paper_wrong` 行的 `question_id` 为 NULL、`ref_id` 是 `wrong_item.id`，无法按「同一题」去重。
**处理**：窗口条目键取 `question_id`，为空时退化为 `ref_id`；一条试卷错题因此天然只出现一次。
不因此把试卷错题排除在窗口外——否则「家长录错题」这条路对薄弱项就彻底失效了。

---

## 6. 本轮范围

**做**：窗口构建、WA 与等级、状态机（立案/维持/关闭/复发）、主因线、证据题、扩表迁移、
T1–T14 测试、把状态机接到现有写入路径（电视作答 / 试卷错题 / 家长调整 / 退题）。

**不做**：报告页面与接口重排、H5 文案、出题侧切换到 15 条窗口、历史数据回算。
生产 `weakness` 当前为 0 行（2026-11-21 迁移后清空），所以本轮**不需要数据回填**。

## 7. 落点

| 内容 | 文件 |
|---|---|
| 计算与状态机 | `server/app/weakness.py`（新） |
| 扩表迁移 | `server/db/migrate-2026-11-21-weakness-state.sql`（新） |
| 双份 schema | `server/db/schema.sql`、`server/db/schema.sqlite.sql` |
| 写入接线 | `server/app/mastery.py` 的 `_store()` |
| 测试 | `knowledge-eval/tests/test_weakness_calc.py`（新） |
