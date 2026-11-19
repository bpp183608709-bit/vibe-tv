# 验证说明

从仓库根目录跑，按顺序。除特别注明外都只用标准库（`pytest` 需要装一次：`python -m pip install --user pytest`）。

---

## 1. 数据直接过 schema

```bash
python knowledge-eval/scripts/check_schema.py
```

```
schema: 90 nodes, 67 questions checked against knowledge-eval/schemas
schema: ok
```

这一步之所以有意义，是因为**没有中间层**：`check_schema.py` 把 `json.load` 得到的对象原样交给校验器。若字段还叫 `name_zh`、`micro_nodes` 还是对象数组，这里就会失败——没有地方可以补一层翻译。

失败样例（删掉一个诊断节点的 `node_type`）：

```
  x nodes_math_6up.json[1] math-6up-d01: $: missing required field 'node_type'
schema: 1 problem(s)
```
（由 `tests/test_schema_direct.py::test_missing_node_type_fails` 断言）

---

## 2. 双向内容校验

```bash
python knowledge-eval/scripts/check_content.py
```

```
nodes: 27 aggregate, 63 diagnostic
  math     25 nodes  15 questions  {'易': 5, '中': 5, '难': 5}  ...
  chinese  22 nodes  36 questions  {'易': 12, '中': 12, '难': 12}  ...
  english  16 nodes  16 questions  ...
coverage pep-math-6up-v3.json: 30/30 micro points referenced
  mt_g6u_001  math-6up-d01     分数乘整数的意义
  ...
  mt_g6u_034  math-6up-d24     节约用水的数据统计
  mt_g6u_035  math-6up-d14     刘徽割圆术的数学思想
  mt_g6u_036  math-6up-d25     分数四则混合运算      ← P0 补回的那一个
  ...
content: ok
```

`mt_g6u_036` 这一行就是 P0 的证据：既在快照里，也被一个诊断节点引用，且总数是 30/30（v2 时代是 38/38）。

失败样例（删掉 `source_ids` 里的 `mt_g6u_036`）：

```
  x coverage: snapshot pep-math-6up-v3.json has 30 micro points, nodes reference 29; missing: mt_g6u_036
content: 1 problem(s)
```

失败样例（加一个快照里没有的 id）：

```
  x coverage: nodes reference 1 micro point(s) the snapshot does not have: mt_g6u_999
content: 1 problem(s)
```

两个都由 `tests/test_check_content_negative.py` 断言；第三个测试把快照砍到 10 条，证明校验不依赖 `38` 这个硬编码。

题量规则是**按节点**的上限而不是按学科：`MAX_PER_NODE = 6`（稳健档 2 易 + 2 中 + 2 难）。
原来的 `MAX_PER_SUBJECT = 18` 在语文 22 个诊断节点、36 道题时直接失败——它此前能通过，
靠的是十个节点一道题都没有，而那正是题库要避免的状态。负样本把 `math-6up-d01` 堆到 9 道题，
断言输出里出现 `math-6up-d01: 9 questions`。

---

## 3. 课本目录差集（outline vs 节点）

```bash
python knowledge-eval/scripts/check_outline_coverage.py
```

```
outline: 3 subject(s) from knowledge-eval/data
  math      9 units ·  0 texts ·  0 boards   covered 9/9 · gap 0 · 不测 0
  chinese   8 units · 26 texts · 19 boards   covered 42/42 · gap 0 · 不测 11
  english  10 units ·  0 texts · 13 boards   covered 10/14 · gap 4 · 不测 9
gap (课本有、节点没提):
  - english Let's Know More 1 — 没有节点提到它
  …
outline: ok (4 gap(s) reported as work not done)
```

`data/outline_<subject>_6up.json` 就是课本目录，只到三层：**单元 / 课文标题 / 板块名**。再往下是小节，
各家教辅划分互不一致，做不出一致的基准——而基准错了，校验越严格错得越离谱（2026-11 那次三科全错配
就是抄了网络目录，见 `textbook-coverage-2026-11-19.md` §修订说明）。

五类判决：

| # | 判决 | 例子 | 退出码 |
|---|---|---|---|
| 1 | 漏（outline − 节点） | 课本有《草原》，没有节点提到 | 0（`--strict` 时 1）|
| 2 | 错指（节点 − outline） | 节点微观点里出现《桂树》 | 1 |
| 3 | 单元名不一致 | 聚合节点叫「位置与方向（二）」，课本印的是「确定位置」 | 1 |
| 4 | 题目引用了课本没有的篇名 | 题干或选项里出现《花之歌》 | 1 |
| 5 | 退役篇名复活 | 微观点里还写着伯牙鼓琴（不带书名号也算） | 1 |

`assessable: false` 的条目（8 次习作、3 次口语交际、英语附录与检测页）**必须写 `reason`**，
写了才不计入 gap。"没建"和"决定不建"是两件事，只有后者是决定。

失败样例（把 `d11` 的微观点换回旧版课文）：

```
  x chinese-6up-d11: still names '伯牙鼓琴', which left this volume
  x chinese-6up-d11: mentions 《伯牙鼓琴》, which is not in the outline
outline: 2 problem(s)
```

由 `tests/test_outline_coverage_negative.py` 的 7 条用例断言：漏 / 错指 / 退役篇名 / 题干篇名 /
单元名不一致 / 不测条目不写理由 / 干净数据通过。

---

## 4. 负样本与回归测试

```bash
python -m pytest knowledge-eval/tests/ -v          # 仓库根目录
cd knowledge-eval && python -m pytest tests/ -v    # 或在 knowledge-eval 里跑
```

```
95 passed
```

| 文件 | 断言什么 |
|---|---|
| `test_check_content_negative.py` | 缺引用 / 多引用 / 换快照 / 节点题数超上限，脚本都必须非零退出并点名 |
| `test_outline_coverage_negative.py` | 漏 / 错指 / 退役篇名 / 题干引用了课本没有的篇名 / 单元名不一致 / 不测不写理由 |
| `test_schema_direct.py` | 缺字段 / 数组形状错 → schema 失败；校验器收到的是原始 dict；`flat_nodes` 不存在 |
| `test_report_weak_only.py` | 默认只列 L0/L1；`--include-full-view` 时 L2/L3 进 `full_view`；`assert_report_weak_only.py` 对篡改过的报告报错 |

---

## 5. 离线报告（不连数据库）

```bash
python knowledge-eval/scripts/evaluate.py \
  --nodes knowledge-eval/data/nodes_math_6up.json \
  --questions knowledge-eval/data/questions_math_6up.json \
  --answers knowledge-eval/tests/fixtures/sample_answers_mixed_levels.json \
  --out /tmp/report.json

python knowledge-eval/scripts/assert_report_weak_only.py /tmp/report.json
```

```
report: 10 chapters, 1 weak node(s), scope=weak -> /tmp/report.json
weak-only: ok (1 weak node(s), every one 0-未掌握 or 1-基础)
```

fixture 里刻意放了四种状态：`d01` 三道全错（L0）、`d08` 三对但没答对难题（L2）、`d11` 两道难题答对（L3）、`d15` 只有一条作答（不判）。所以「只出 1 个薄弱」本身就是断言。

全貌视图：

```bash
python knowledge-eval/scripts/evaluate.py ... --out /tmp/full.json --include-full-view
# → report: 10 chapters, 1 weak node(s), scope=all -> /tmp/full.json
```

此时 `full_view` 里能看到 `2-熟练` / `3-拓展`，而 `diagnostics` 仍然只有 `0-未掌握`。

---

## 6. 数据库形态与内容规则

需要库里有数据（`QUIZ_DSN`，或本机 `server/local/` 下的 sqlite）：

```bash
python knowledge-eval/scripts/check_db_parity.py --content
```

```
db parity: ok (27 tables, 259 columns, content rules hold)
```

`--content` 跑的是评审要求的那几条 SQL：

| 规则 | SQL |
|---|---|
| 题目不指向章节 | `SELECT COUNT(*) FROM question WHERE unit_id IS NOT NULL` = 0 |
| 也 reach 不到章节 | `SELECT COUNT(*) FROM question q JOIN unit u ON u.id = q.unit_id` = 0 |
| 每题有 subject | `SELECT COUNT(*) FROM question WHERE subject_id IS NULL` = 0 |
| 每题有业务键 | `SELECT COUNT(*) FROM question WHERE code IS NULL` = 0 |
| 节点有稳定 id / 章节序 | `topic.code IS NULL` = 0，`topic.chapter_order = 0` = 0 |
| 章节有稳定 id | `unit.code IS NULL` = 0 |

---

## 7. 数据库导出与仓库文件逐字段比对

```bash
python knowledge-eval/scripts/export_from_db.py --check
```

```
export: ok — 34 nodes, 30 nodes, 26 nodes; 15 questions, 36 questions, 16 questions (checked, not written)
```

`--check` 不写文件，只把库里的行重新投影成 §4.1 / §4.2 的形状，和 `knowledge-eval/data/` 里的文件逐字段比。它比「导回去再导入一遍」强：后者两边共用同一个 bug 时仍然会通过。

---

## 8. 端到端验收（改动服务端时跑）

```bash
python -m server.tools.acceptance
```

```
acceptance: 256/256 passed  (db acceptance-<stamp>.sqlite3)
```

覆盖：配对、出题、判分、工单、错题本、掌握度、配额混合、两级报告、家长页、账号、排课、作废题目。

---

## 9. 逐格齐备（能力线）

判档的粒度在 2026-11-19 从「节点」扩到「节点 × 能力线」：每条线有自己的十条窗口，自己的档位，
自己的 `ability_mastery` 行。节点级 `topic_mastery` 不变，薄弱项清单、每日配额、复习段仍按节点算。

为什么必须分表：四条线共用节点的十行窗口时，四条线各要 MIN_SAMPLE(3) 就是十二行，装不进十行，
滚动窗口还会把最早的挤出去。这不是题量问题，`knowledge-eval/tests/test_ability_mastery.py` 里
`test_each_line_keeps_its_own_window` 用十二条作答把这件事钉住了——节点窗口剩十条（某条线掉到 2 条，
判不出档），而四行 `ability_mastery` 各留 4 条。

```bash
python knowledge-eval/scripts/check_ability_coverage.py            # 报告，恒退出 0
python knowledge-eval/scripts/check_ability_coverage.py --strict   # 有不齐的格子就退出 1
python knowledge-eval/scripts/check_ability_coverage.py --subject math --detail
```

```
cells 205: 17 floor, 0 robust, 51 partial, 137 missing
  math      96 cells  floor  16  robust   0  partial  12  missing  68  nodes ready 4/25
  chinese   64 cells  floor   1  robust   0  partial  27  missing  36  nodes ready 1/22
  english   45 cells  floor   0  robust   0  partial  12  missing  33  nodes ready 0/16
top-up to floor: 503 questions (易 167, 中 167, 难 169)
top-up to robust: 1118 questions (易 372, 中 372, 难 374)
```

格子数不是 63×4=252，而是 205：每个诊断节点在 `ability_lines` 里声明它真能被问到哪几条线。
「用有序数对确定位置」没有可执行的计算，「文言朗读停顿」没有习作任务，把它们算进矩阵只会逼着
出题人硬凑。声明了就必须补题，所以这份清单是**承诺**，不是注释。

`--strict` 是这个批次的验收门：一批题生成完，先跑它，再上库。

---

## 10. 已知限制

- `check_content.py` 的反向覆盖只对**有快照**的科目生效（目前只有数学）。语文/英语没有公开分类法，`source_ids` 为空，反向集合为空集相等，不构成漏检。
- 差集只回答「课本里有没有」，不回答「题目对不对」：节点覆盖了《两小儿辩日》就算过，题目仍可能按旧篇目出题。
  第 4 条规则（题干与选项里的书名号）只覆盖孩子能看到的文字，**不扫解析**——解析里引用《呐喊》《朝花夕拾》
  这类课外作品是正常的，扫了会到处都是误报。
- 英语还有 4 条 gap（Let's Know More ×3、Let's Read More）：课本有这个板块，但还没有节点。它们是待建，不是漏检。
- `export_from_db.py` 不导出题目的 `tags`（写作元数据，库里不存），`--check` 会跳过该字段。
- `check_db_parity.py` 需要能连上目标库；没有库时请先跑 `acceptance`（它会建一个临时 sqlite 并导入全部内容）。
- **旧的本地 sqlite 库不能直接升级**：`server/tools/migrate.py` 会补新列和新表，但 SQLite 改不了 `question.unit_id` 的 `NOT NULL`。本机开发库请删掉重建（`Database(init_schema=True)`），生产 MySQL 用 `migrate-2026-11-19-flat-nodes.sql` 里的 `MODIFY COLUMN`。
