# 英语稳健档（3/3/3）批次计划 · 2026-11-20

目标：英语 45 个格子从 `floor`（每格 1/1/1）补到 `robust`（每格 3/3/3）。
每格补 6 题，共 **270 题**，做完英语 477 题（207 原有 + 270 补入）。

`d18`–`d21`（阅读拓展板块四个专题）在 E6 那一批已到 3/3/3，本轮不补。

## 英语的三处不同

| 项 | 数学 | 语文 | 英语 |
|---|---|---|---|
| 题型 | 选择 / 判断 / 填空 | 只有选择题 | **只有 4 选项选择题**（存量 207 题全 4 项，判断题与填空题都不用） |
| 答案自校验 | 有 `c` 字段 | 无 | 无 |
| 材料依赖 | 无 | 课外材料自带引文 | 同语文，但英文引文更短，压缩空间大 |
| 选项语言 | 中文 | 中文 | **一题内统一**：全英文（词形/句子）或全中文（释义），不混排 |

## 批次表

每批 1–2 个节点、4–6 格、24–36 题，一个节点一个文件（子代理一次只写 18–24 行）。

| # | 节点 | 格 | 题 | 状态 |
|---|---|---|---|---|
| E01 | `d01` 一般过去时 + `d03` 三单 | 6 | 36 | 待做 |
| E02 | `d02` 假期词汇问答 + `d04` 频度副词 | 6 | 36 | 待做 |
| E03 | `d06` enjoy doing + `d07` 现在时/过去时对比 | 5 | 30 | 待做 |
| E04 | `d08` will + `d09` 天气词汇 | 5 | 30 | 待做 |
| E05 | `d10` 量词 + `d11` can | 4 | 24 | 待做 |
| E06 | `d12` should 环保 + `d13` 比较级 | 5 | 30 | 待做 |
| E07 | `d14` 比较级句型 + `d15` 最高级与节日 | 6 | 36 | 待做 |
| E08 | `d16` 疾病表达 | 4 | 24 | 待做 |
| E09 | `d17` should 看病 | 4 | 24 | 待做 |

合计 9 批、45 格、270 题。

## 新增的一道工序：`check_spec.py`

M1–E6 那十六批的返工全在 B 层（门禁看不见的那层）。语文 C07 起改进了写法，但检查还是靠人。
本轮把 B 层里机器看得见的部分做成脚本，**写题的人自己就能跑**：

```
python3 scripts/check_spec.py batches/robust-english-E01a.jsonl --subject english --per-cell 2
```

硬失败（退出码 1）：

| 规则 | 对应 | 报错 |
|---|---|---|
| 题干 ≤120 字符 | B6 | `over 120` |
| 引文 ≤90 字符 | B6 | `quotation is N characters` |
| 「读句子」类但没引号 | B1 | `does not quote` |
| 出现「根据课文 / 和课文一致」 | B1 | `no textbook` |
| 同 spec 内题干重复 | B4 | `already appears on line` |
| 与全库题干重复 | B4/B10 | `repeats the stem of a question` |
| 解析用裸 A/B/C/D | 1.4 | `points at options by letter` |
| `k` 不是 A | 轮转前提 | `k must be A` |
| 某格题数不等于 `--per-cell` | 分布 | `want N` |

只警告（不拦）：

- `B11: N/M correct options are the unique longest`（>35% 才报）
- `length band`：四选项最长/最短超过 1.5 倍的题，直接列出 `d05:3/3/2/9` 这样的长度序列

英语词形题（`visit / visited / visits / visiting`）天然过不了 1.5 倍带，所以这条只作提醒；
真正要压的是中文释义型选项。

脚本自身有 11 条负样本测试（`tests/test_check_spec.py`），改规则时先跑它。

## 每批工序

```
1  生成 spec            batches/robust-english-E01a.jsonl / E01b.jsonl
2  自检                check_spec.py --subject english --per-cell 2   → spec: ok
3  轮转选项            rotate_options.py <spec>
4  试运行              batch_spec.py --subject english --spec <spec> --dry-run  → 0 rejects
5  写入                batch_spec.py --subject english --spec <spec>
6  四门禁 + pytest + acceptance
7  抽检 6 道（英语无 c 字段，只能人读；重点看引文与干扰项）
8  一提交
```

## 完成定义

- `check_ability_coverage.py --subject english` → `53 cells, robust 53, floor 0, partial 0, missing 0`
- `top-up to robust: 0 questions`
- 四门禁全绿、`pytest` 全过、`acceptance 256/256`
- 全库 213 格全部 robust（数学 96 + 语文 64 + 英语 53）
- 每批 `check_spec` 的 B11 比例 ≤35%
