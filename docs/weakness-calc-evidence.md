# 薄弱项状态机：测试证据与新旧差异（2026-11-21）

> 规则与字段映射见 [`weakness-calc-mapping.md`](weakness-calc-mapping.md)。
> 本文件回答三件事：T1–T14 跑在哪、怎么跑、结果如何；旧实现差在哪；上线前必须做什么。

---

## 1. 怎么跑

```sh
cd /Volumes/个人/code/aerial-tv-main

# 本轮新增的 17 条（T1–T14 + 证据题 + 接线）
python3 -m pytest knowledge-eval/tests/test_weakness_calc.py -q
# 17 passed in 0.77s

# 全量回归（原有 141 + 新增 17）
python3 -m pytest knowledge-eval/tests -q
# 158 passed in 42.29s

# 端到端验收（SQLite，自建空库，不碰生产）
.venv/bin/python server/tools/acceptance.py
# acceptance: 256/256 passed

# 两份 DDL 不许漂移
python3 server/tools/check_schema_parity.py
# schema parity: ok (28 tables, 284 columns)

# 题库侧门禁
python3 knowledge-eval/scripts/check_schema.py
# schema: 95 nodes, 1917 questions checked; ok
```

---

## 2. T1–T14 逐条

测试文件：`knowledge-eval/tests/test_weakness_calc.py`

| 编号 | 测试名 | 断言要点 | 结果 |
|---|---|---|---|
| T1 | `test_t1_wa_over_nine_answers` | 易对 5 / 中对 3 / 难错 1 → n=9、WA=826、不立案 | ✅ |
| T2 | `test_t2_wa_exactly_four_tenths_is_level_one` | WA=400 → L1，立案，`initial_level=1` | ✅ |
| T3 | `test_t3_wa_exactly_six_tenths_is_level_two_and_not_weak` | WA=600 → L2，不立案 | ✅ |
| T4 | `test_t4_wa_exactly_eight_tenths_without_hard_stays_open` | WA=800、hard=0 → L3 候选封顶 L2，`hard_ok=False`，仍 active | ✅ |
| T5 | `test_t5_hard_right_but_two_medium_cannot_close` | WA=800、hard=1、medium=2 → 等级 3，`need_medium=1`，不关闭 | ✅ |
| T6 | `test_t6_three_medium_and_one_hard_closes` | WA=829、hard=1、medium=3 → `resolved`，写入 `closing_snapshot` | ✅ |
| T7 | `test_t7_two_wrong_answers_open_nothing` | n=2 全错 → 无记录 | ✅ |
| T8 | `test_t8_same_question_three_wrong_one_right_counts_once` | 同题 4 条 → 窗口 n=1，无记录；账本仍 4 行 | ✅ |
| **T9** | `test_t9_time_freeze_hard_constraint` | **硬约束**：T+60 天重算，窗口序列、WA、等级、状态、`last_practiced_at`、窗口快照全不变 | ✅ |
| **T10** | `test_t10_easy_questions_push_the_hard_one_out_hard_constraint` | **硬约束**：15 道易题把难题挤出后 WA=1.0，但 `hard_correct=0`、`medium_correct=0`，不关闭 | ✅ |
| T11 | `test_t11_relapse_reopens_and_counts` | 关闭后 WA 跌到 0.54 → 重新 active，`reopen_count=1`，`opened_at` 保留，旧快照进 `detection_history` | ✅ |
| T12 | `test_t12_never_answered_creates_nothing` | 零作答 → 无记录，`reason=no-window` | ✅ |
| T13 | `test_t13_level_moves_up_without_closing` | L1→L2（WA 0.667），无难题 → 不关闭，仍 active | ✅ |
| T14 | `test_t14_cause_lines_break_ties_by_hard_misses` | 两条线同 0 级 → 难题答错多的排前；`cause_status=ok` | ✅ |
| T14b | `test_t14b_no_line_reaches_two_samples` | 三条线各 1 条 → `insufficient_sample`，`cause_tags=[]` | ✅ |
| 附 | `test_evidence_is_wrong_questions_from_the_cause_line` | 只取错题；主因线优先 → 难度降序 → 时间降序；≤3 道 | ✅ |
| 附 | `test_record_tv_answer_path_writes_the_state_machine` | 走真实 `mastery.record_tv_answer`，三错后写入节点 code 与章节名 | ✅ |

T9、T10 在文件里以 `_hard_constraint` 结尾命名，跑 `pytest -k hard_constraint` 可单独挑出来：

```sh
python3 -m pytest knowledge-eval/tests/test_weakness_calc.py -k hard_constraint -q
# 2 passed
```

---

## 3. 与旧实现的差异

| 维度 | 旧实现（`mastery._sync_weakness`） | 本轮 |
|---|---|---|
| 窗口 | 最近 **10 条作答**，同一题做 5 次算 5 条 | 最近 **15 道不同题**，同一题只留最近一次 |
| 等级边界 | `acc <= 800` → L2 | `>= 800` 即 L3 候选（仍需一道难题答对） |
| 立案 | 已测（≥3 条）且 `level <= 1`，即 WA < 0.6 | 同为 WA < 0.6，但基于 15 题去重窗口 |
| 关闭 | `level > 1` 就置 `resolved`，**WA 到 0.6 就关** | `WA≥0.8` **且** `hard_correct≥1` **且** `medium_correct≥3`，三项都在窗口内计数 |
| 关闭后的 0.6–0.8 区间 | 视为已恢复 | **仍为 active**（本轮最大的行为变化） |
| 主因线 | 无（能力线只是节点窗口按 tag 切片，且切片后每线只有 1–2 条） | 立案时按标签独立成窗（≥2 条）取最弱两条，冻结 |
| 证据题 | 窗口内最新 3 道错题的 code | 主因线优先 → 难题优先 → 新题优先，≤3 道 |
| 快照 | 只有 `mastery_level / weighted_accuracy / sample_count` 三个当前值 | 立案快照、当前窗口快照、关闭快照、历次复发历史都落库 |
| 复发 | 只把 `resolved` 清 0，无计数、无历史 | `reopen_count`、`reopened_at`、`detection_history` 追加旧快照 |
| 时间冻结 | 天然成立（没有定时任务） | 同样成立，并由 T9 显式守住 |
| 写入点 | `mastery._store()` | 仍是 `mastery._store()`，单一入口，不会双写 |

**没有改的**：`mastery.MASTERY_WINDOW=10`、出题档位、配比、`need_right`、暂停阈值、H5 接口形状。
所以同一个节点暂时有两个等级：`topic_mastery.level`（出题用，10 条按次）与
`weakness.mastery_level`（报告用，15 条去重）。两者只在重复题多、或样本刚过 3 条时不同。
合并口径留给报告轮，见映射文档 §5.1。

---

## 4. 上线前必须做

1. **先跑迁移，再发代码**。新代码会往 `weakness` 写 11 个新列，缺列会让判分事务整体回滚：
   ```sh
   mysql -uroot quiz < server/db/migrate-2026-11-21-weakness-state.sql
   ```
   迁移是纯加法，旧代码在加完列之后照常工作，所以可以先迁移、后发代码。
2. **不需要回填**。生产 `weakness` 在 2026-11-21 迁移后是 0 行，新状态机会从第一条作答开始记账。
3. 本轮**没有部署**：本地通过 158 条 pytest 与 256/256 验收，生产仍是旧代码 + 旧表结构。
   发版后第一组题做完，可以直接查：
   ```sql
   SELECT diagnostic_node_id, mastery_level, resolved, cause_status, reopen_count
     FROM weakness WHERE device_id = '<电视>' ORDER BY updated_at DESC;
   ```
