# Knowledge Evaluation System

小学六年级上册（人教版数学 / 统编版语文 / 湘少版英语）的知识掌握度评测：一套节点数据、一套题目数据，用「最近十条作答按难度加权」算出掌握档位，再产出一份「这周该补什么」的薄弱清单。

这个目录是**外部审查者的入口**：数据、schema、脚本、测试、验证说明都在这里。运行时服务端仍然在 `server/`（不物理搬迁），下面的路径表写清了每个文件真正在哪。

---

## 1. 两分钟跑通

从仓库根目录执行（只需要 Python 3.9+ 标准库；跑测试再装一个 pytest）：

```bash
# 1) 原始数据直接过 schema（没有翻译层）
python knowledge-eval/scripts/check_schema.py
# → schema: 95 nodes, 687 questions checked against knowledge-eval/schemas
# → schema: ok

# 2) 双向内容校验：快照 30 个微观点 vs 节点引用
python knowledge-eval/scripts/check_content.py
# → nodes: 28 aggregate, 67 diagnostic
# → coverage pep-math-6up-v3.json: 30/30 micro points referenced
# →   mt_g6u_036  math-6up-d25     分数四则混合运算      ← 上一版丢掉的那一个
# → content: ok

# 3) 课本目录差集：课本有、节点没提（漏）/ 节点提了、课本没有（错指）
python knowledge-eval/scripts/check_outline_coverage.py
# → chinese 42/42 · math 9/9 · english 12/12（Let's Know More 与 Let's Read More 已建节点）
# → outline: ok (0 gap(s) reported as work not done)

# 4) 逐格齐备：每个（节点 × 声明的能力线）格子能不能构出四个阶段
python knowledge-eval/scripts/check_ability_coverage.py
# → cells 213: 205 floor, 8 robust, 0 partial, 0 missing
# → top-up to floor: 0 questions（最低档已齐；稳健档每格 3/3/3 另计）
python knowledge-eval/scripts/check_ability_coverage.py --strict   # 同上，但不齐就退出 1

# 5) 负样本：把引用删掉/加错，校验必须失败（不含规格测试）
python -m pytest knowledge-eval/tests/ -q --ignore=knowledge-eval/tests/test_four_stage_constructible.py
# → 30 passed

# 6) 离线报告：不连数据库，纯文件
python knowledge-eval/scripts/evaluate.py \
  --nodes knowledge-eval/data/nodes_math_6up.json \
  --questions knowledge-eval/data/questions_math_6up.json \
  --answers knowledge-eval/tests/fixtures/sample_answers_mixed_levels.json \
  --out /tmp/report.json
python knowledge-eval/scripts/assert_report_weak_only.py /tmp/report.json
# → weak-only: ok (1 weak node(s), every one 0-未掌握 or 1-基础)

# 7) 数据库形态 + 内容规则（需要库里有数据；默认读 QUIZ_DSN 或 server/local/）
python knowledge-eval/scripts/check_db_parity.py --content
# → db parity: ok (28 tables, 273 columns, content rules hold)

# 8) 数据库导出与仓库文件逐字段比对
python knowledge-eval/scripts/export_from_db.py --check
# → export: ok — 34 nodes, 30 nodes, 26 nodes; 288 questions, 192 questions, 135 questions
```

全部命令的预期输出与失败样例见 [docs/verification.md](docs/verification.md)；逐条修复证据见 [docs/fix-evidence-2026-11-19.md](docs/fix-evidence-2026-11-19.md)。

### 1.1 覆盖审查：题库够不够撑起四阶段

```bash
# 1) 生成覆盖矩阵（252 个 诊断节点 × 能力标签 格子）
python knowledge-eval/scripts/check_coverage_matrix.py
# → cells 252: 190 without a question, 0 measured (n>=3), 0 floor-ok, 0 robust-ok
# → target chapter math-6up-u02 分数乘法 (4 diagnostics)
# → top-up to floor: 689 questions; to robust: 1445
# → 写 reports/coverage-matrix.json 与 reports/coverage-matrix.md

# 2) 负样本：审查脚本必须能判负
python -m pytest knowledge-eval/tests/test_coverage_matrix_negative.py -v
# → 3 passed

# 3) 规格测试：题库不达标时出现预期的 xfail
python -m pytest knowledge-eval/tests/test_four_stage_constructible.py -v
# → 1 passed, 48 xfailed

# 4) 常规回归必须仍全绿
python -m pytest knowledge-eval/tests/ -q --ignore=knowledge-eval/tests/test_four_stage_constructible.py
# → 28 passed
```

结论与补题清单见 [docs/coverage-audit-2026-11-19.md](docs/coverage-audit-2026-11-19.md)：
当前 **0 / 252 个格子达到最低档**，补齐到最低档需 689 题（目标章节 `math-6up-u02` 分数乘法）。

### 1.2 课本结构覆盖：节点够不够撑起"整册"

上面查的是"题够不够"，这里查的是"节点全不全"——28 个聚合 + 67 个诊断，是否覆盖了三科六年级上册的课本结构。

结论见 [docs/textbook-coverage-2026-11-19.md](docs/textbook-coverage-2026-11-19.md)。**这一版以课本原件（家长拍摄的目录页）为准**，推翻了初版两条凭网络目录得出的结论。三句话：

- **数学已按 2024 新版重排**：9 个顶层条目（6 单元 + 3 个 ☆ 主题活动）按课本顺序排好；"比""扇形统计图""数与形"三个单元连同 6 个诊断节点、3 道题移到 `data/deferred/`（不删、不导入）；补了负数、用有序数对确定位置、用百分数比较数据、鸽巢问题等 6 个新节点；顺带修掉 3 处先修成环。
- **语文已按新教材换过课文**：旧版 5 篇（夏天里的成长、伯牙鼓琴、书戴嵩画牛、月光曲、京剧趣谈）已不在六上，`d03` `d08` `d11` `d12` 与 4 道题已改成 26 课里的篇目；7 个语文园地 + 快乐读书吧已补成 8 个板块节点（`d15`–`d22`），语文 14 → 22 个诊断节点、12 → 36 道题。
- **英语已按 2026 秋新版重建**：删掉新版不存在的《I like my computer》（含 `d05 There be`），补上 U9《I don't feel well》（`d16` 疾病表达 / `d17` 看病建议），`enjoy doing` 迁进 U3 中秋节，10 个单元名与 `chapter_order` 全部对齐课本；新单元配了 4 道题，不是空壳。

课本目录已固化成 `data/outline_<subject>_6up.json`（三层：单元 / 课文标题 / 板块名），`scripts/check_outline_coverage.py` 做双向差集，另加三条：单元名逐字一致、题目引用的篇名必须是课本里的、退役篇名不得复活。这一节从此可回归——限定条件与未覆盖的边界见该文档 §6 与 `docs/verification.md` §3。

写题的硬约束汇总在 [docs/question-rules-2026-11-20.md](docs/question-rules-2026-11-20.md)：门禁能拦的 20 余条列在 §1（跑脚本即可），真正要背的是 §2 那十条——M1–E6 十六批里的返工全部发生在门禁看不见的地方。

---

## 2. 目录

直达：[data/](data/) · [schemas/](schemas/) · [scripts/](scripts/) · [tests/](tests/) · [reports/](reports/) · [docs/](docs/)

```
knowledge-eval/
├── README.md            ← 你在这里
├── data/
│   ├── nodes_math_6up.json        34 个节点（9 聚合 + 25 诊断）
│   ├── nodes_chinese_6up.json     30 个节点（8 + 22）
│   ├── nodes_english_6up.json     26 个节点（10 + 16）
│   ├── questions_math_6up.json    15 题
│   ├── questions_chinese_6up.json 36 题
│   ├── questions_english_6up.json 16 题
│   ├── outline_math_6up.json      课本目录：9 个顶层条目（6 单元 + 3 主题活动）
│   ├── outline_chinese_6up.json   课本目录：8 单元 / 26 课 / 19 板块 + 6 个退役篇名
│   ├── outline_english_6up.json   课本目录：10 单元 + 13 个板块
│   ├── deferred/                  暂存：六下的比/扇形统计图/数与形（不校验、不导入）
│   └── taxonomy/pep-math-6up-v3.json   人教版数学六上微观点快照（30 点 / 40 边）
├── schemas/
│   ├── node.schema.json
│   ├── question.schema.json
│   ├── outline.schema.json
│   └── report.schema.json
├── scripts/
│   ├── keval.py                共用的读取与 schema 校验（纯文件 I/O）
│   ├── check_schema.py         原始对象直接校验
│   ├── check_content.py        双向覆盖 + 题目规则 + 题量覆盖
│   ├── evaluate.py             离线报告（不连库）
│   ├── export_from_db.py       从库里导出三类 JSON，并可与仓库文件比对
│   ├── assert_report_weak_only.py
│   ├── check_db_parity.py      表/列形态 + 内容规则
│   ├── check_outline_coverage.py 课本目录差集（漏 / 错指 / 单元名 / 题干篇名 / 退役篇名）
│   └── check_coverage_matrix.py  (诊断节点 × 能力) 四阶段可达矩阵 + 补题清单
├── tests/
│   ├── fixtures/sample_answers.json
│   ├── fixtures/sample_answers_mixed_levels.json
│   ├── test_check_content_negative.py
│   ├── test_schema_direct.py
│   ├── test_report_weak_only.py
│   ├── test_coverage_matrix_negative.py    覆盖审查的负样本（3 条）
│   ├── test_outline_coverage_negative.py   课本目录差集的负样本（7 条）
│   └── test_four_stage_constructible.py    规格测试：当前 1 passed / 48 xfailed
├── reports/                     由脚本生成，勿手改
│   ├── coverage-matrix.json     全部 205 个格子
│   └── coverage-matrix.md       汇总 / 逐格矩阵 / 缺口清单
└── docs/
    ├── data_sources.md         三科版本、逐节点来源、形态说明
    ├── verification.md         每条命令的预期输出与负样本
    ├── fix-evidence-2026-11-19.md   逐条修复证据表
    ├── coverage-audit-2026-11-19.md 题库四阶段覆盖审查（0/220）与补题清单
    ├── textbook-coverage-2026-11-19.md 三科课本结构逐条对照与执行记录
    └── question-rules-2026-11-20.md 生题硬约束（16 批的踩坑汇总，写题前必读）
```

---

## 3. 运行时在哪（路径表）

数据与校验脚本搬到了这里，**运行时没有被复制一份**，仍然只有一份实现：

| 能力 | 实际文件 | 说明 |
|---|---|---|
| 节点 / 题目数据 | `knowledge-eval/data/*.json` | 唯一来源；导入器直接读这里 |
| 导入节点 | `server/tools/import_curriculum.py` | 按 `node_id` 幂等 upsert |
| 导入题目 | `server/tools/import_questions.py` | `options` 是字符串数组，label 按位置生成 |
| 掌握度模型 | `server/app/mastery.py` | 滚动窗口、加权正确率、档位、`weakness` 同步 |
| 报告结构 | `server/app/report.py` | `scope='weak'` 默认，`all` 才带 `full_view` |
| 报告取数 | `server/app/h5.py` `report_by_node()` | 薄弱行读 `weakness` 表 |
| HTTP | `GET /v1/h5/mastery/report[?scope=all]` | `server/app/api.py` |
| 出题 | `server/app/picker.py` | `topic.chapter_order`，不再 join `unit` |
| 表结构 | `server/db/schema.sql` · `schema.sqlite.sql` | 27 表 / 259 列，两边列级一致 |
| 迁移 | `server/db/migrate-2026-11-19-flat-nodes.sql` | 只加不删；`question.unit_id` 置空 |
| 端到端验收 | `server/tools/acceptance.py` | 256 条 |

`evaluate.py` 与 `report.py` 的分工：前者只把「作答文件」变成「报告行」，后者决定报告的字段与过滤；两边共用 `mastery.summarise` / `mastery.level_of`，所以算错的话两边一起错——那才会被发现。

---

## 4. 模型（一屏说完）

| 难度 | 权重 ×10 |
|---|---|
| 易 | 10 |
| 中 | 15 |
| 难 | 20 |

取每个 `(学生, 诊断节点)` 最近 10 条作答，`acc_x1000 = round(1000 × 加权正确 / 加权总数)`：

| 档 | 条件 | 报告里的字 |
|---|---|---|
| — | `sample < 3` | 不判（不出现在报告里） |
| 0 | `acc < 400` | 0-未掌握 |
| 1 | `400 ≤ acc < 600` | 1-基础 |
| 2 | `600 ≤ acc ≤ 800` | 2-熟练 |
| 3 | `acc > 800` 且至少答对 1 道难题 | 3-拓展 |

报告默认只列 0 和 1；`2-熟练` / `3-拓展` 只在 `scope=all` 的 `full_view` 里出现。

---

## 5. 数据形状

节点（`nodes_*.json` 的 `nodes[]`）：§4.1 的扁平字段，一条一个节点，`node_type` 区分 `aggregate` / `diagnostic`，`parent_id` 指章节，`chapter_order` 是章节顺序，`micro_nodes` 是**字符串数组**。

```json
{
  "node_id": "math-6up-d25",
  "name": "分数四则混合运算",
  "subject": "math",
  "grade": 6,
  "semester": "up",
  "node_type": "diagnostic",
  "parent_id": "math-6up-u-review",
  "chapter_name": "总复习",
  "knowledge_type": "procedural",
  "micro_nodes": ["分数四则混合运算"],
  "source_ids": ["mt_g6u_036"],
  "prerequisite_node_ids": ["mt_g6u_034"],
  "description": "先乘除后加减、有括号先算括号，带分数也能按顺序算对。",
  "status": "active",
  "chapter_order": 10
}
```

题目（`questions_*.json` 的 `questions[]`）：`options` 是**字符串数组**，label 就是 `A + 下标`，`correct_answer` 对选择题填这个 label，对填空题填标准答案（并等于 `accepted[0]`）。

---

## 6. 数据来源

三科教材版本、单元、微观点来源与更新时机见 [docs/data_sources.md](docs/data_sources.md)。一句话：数学来自 vendored 的 38 点分类法快照，语文/英语的微观点是课文/语法点清单，没有公开分类法，因此 `source_ids` 为空。
