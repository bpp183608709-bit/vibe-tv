# 知识树的数据来源

> **这份文档回答三个问题**：这些单元和小节是从哪来的、按什么规则归并成「诊断节点」、什么时候该重取一次。
> 数值规则不在这里（见 `docs/RULES.md`），题目怎么出也不在这里（见 `docs/QUIZ.md` §6）。

## 1. 一句话

```
教材目录（三科各自的官方版本）
        │  人工归并：把一课/一小节拆成能被 3 道题问清楚的「诊断节点」
        ▼
server/content/curriculum/grade6-up.json    27 单元 / 53 节点
        │  import_curriculum.py
        ▼
textbook → unit（聚合节点）→ topic（诊断节点）→ question
```

数学多一层：教材小节之下还有**微观知识点**，来自一份 vendored 的数学知识图谱快照，
它为 24 个数学节点提供了 38 个微观点和 55 条先修关系。

## 2. 三科的版本与来源

| 科目 | 版本 | 规模 | 依据 | 状态 |
|---|---|---|---|---|
| 数学 | 人教版（`publisher='pep'`）六年级上册 | 9 章 / 24 个诊断节点 | 教材目录 + 下方数学知识图谱快照 | 单元列表已核，节点措辞待人校 |
| 语文 | 统编版（人教社出版，`pep`）六年级上册 | 8 个单元 / 14 个诊断节点 | 教材目录（三个教育平台目录互校） | 同上 |
| 英语 | 湘少版六年级上册，**2026 新版** | 10 个单元 / 15 个诊断节点 | 2026 版教材目录 | 同上 |

**两处与早期设计稿不同的地方，都是核对课本后改的**：

1. **语文是 8 个单元，不是 6 个**。设计稿漏了第七单元「艺术之美」和第八单元「走近鲁迅」；
   另外第四单元应含《金色的鱼钩》，第六单元是 18 古诗三首。按 6 个单元建树会让学期后三分之一没有节点可挂。
2. **英语是 10 个单元（2026 新版），不是旧版的 12 个模块**。旧版 12 模块的书市面上还在流通，
   两个版本的单元名完全不同，混用会让家长在 H5 上看到自己孩子课本里没有的单元名。

数学没有这个问题，但设计稿列的 7 章与教材目录也不一致：`mt_g6u_032/033` 属于「数学广角—数与形」，
`024/034` 属于「综合与实践」，都不是独立章节，已并入相应单元。

## 3. 数学：vendored 知识图谱快照

```json
{
  "source": "github:xiaowen-pm/pep-primary-math-taxonomy",
  "source_path": "data/6-up",
  "version": "pep-primary-math-6up-v2",
  "fetched_at": "2026-11-18",
  "topic_count": 38,
  "edge_count": 55
}
```

- 落地文件：`server/content/taxonomy/pep-math-6up-v2.json`（39 KB）。
- **运行时不联网**。导入器只读这个文件；只有人工重新取快照时才碰网络。
  理由很实际：电视那台机器在客厅，它启动失败时家长只会看到「打不开」，不会去查 GitHub。
- 快照里每个微观点带 `name / description / evidence[] / assessment_prompt`。
  数学节点的 `micro_nodes[]` 直接引用它们（`source_id` 形如 `mt_g6u_003`），
  所以「这个节点到底考什么」可以一路追到原始条目。
- 55 条依赖边中，**44 条的两端都在本册内**；归并到节点粒度后得到 **29 条节点间先修关系**，
  已写进节点的 `prereq[]`（存兄弟节点 code，不是微观点 id）。多条微观点边会塌缩成同一条节点边。
  跨册的边（指向五年级内容）没有落库：那一册不在本期范围内，指向它会得到一个空引用。

**重新取快照的步骤**（需要网络）：

```bash
curl -s https://raw.githubusercontent.com/xiaowen-pm/pep-primary-math-taxonomy/main/data/6-up/topics.json
curl -s https://raw.githubusercontent.com/xiaowen-pm/pep-primary-math-taxonomy/main/data/6-up/dependencies.json
# 把两者合成一个 {source, version, fetched_at, topics[], dependencies[]}
python -m server.tools.check_content      # 节点引用的 source_id 必须仍能对上
```

`check_content.py` 会在 `micro_nodes[].source_id` 找不到时直接失败，所以快照换了版本而节点没跟着改，
是能被挡住的。

## 4. 从「教材的一课」到「一个诊断节点」

归并规则只有一条：**一个节点必须能被 3 道题问清楚，并且答错能指向同一个补救动作。**

| 做法 | 为什么 |
|---|---|
| 一课拆成 1–2 个节点 | 长课文（如《草原》）一课里有字词、写法、主旨三件事，合成一个节点会让「错在哪」无法回答 |
| 相邻小节点合并 | 「分数乘整数」和「分数乘分数」算法同源、错误同源，拆开会让两边都凑不满 3 道题 |
| 单元不挂题 | 单元的职责是「现在学到第几周」和报告分组，一旦挂题就会出现「这题属于整章」这种无法判档的情况 |

节点总数 53，而手写题库当前是 39 道题（13 个节点），所以**大多数节点会长期停在「待测」**。
这是刻意的取舍，不是缺数据：用 1 道题的样本去说「他没掌握」，比不显示更糟（`docs/RULES.md` §9 第 9 条）。

## 5. 校验与导入

```bash
python -m server.tools.check_content          # 两个 JSON 对 schema + 覆盖规则
python -m server.tools.import_curriculum      # 写 textbook / unit / topic，幂等
python -m server.tools.import_questions       # 写 question / options / answers，幂等
```

`check_content.py` 挡的是这些：

- 两个 JSON 各字段的类型与必填（`server/content/schema/*.schema.json`，自带无依赖校验器 `schema_check.py`）；
- `code` / `id` 全局唯一，题目只能挂在存在的诊断节点上；
- 选择题的 `correct_answer` 必须在 `labels` 里；填空题的 `accepted[0]` 必须等于 `correct_answer`；
- **非数学的填空题直接报错**——电视键盘没有字母和中文输入法（`QuizScreen.kt:432-441`）；
- 每科 10–15 题、至少覆盖 3 个节点、每个节点至少 2 种 `ability_tag` 且含 1 道中档题；
- 总量 30–45 题。

两个导入器都按业务键做幂等（`topic.code`、`(topic_id, template_key='hand', param_hash)`），
重复跑不会产生重复行，改题库只需要改 JSON 再跑一遍。

## 6. 什么时候该更新这份快照或这棵树

| 触发 | 动作 |
|---|---|
| 教材换版（比如英语又出新版） | 改 `grade6-up.json` 的单元与节点，重跑 `check_content` + 重导；旧题会被 `reset_content.py` 清掉 |
| 数学图谱升到 v3 | 重新取快照，比对 `topics[].id`，改节点的 `micro_nodes[].source_id` 与 `prereq[]` |
| 家长反馈「课本里没这个单元」 | 先核课本目录再改树，不要按 H5 上的显示改——H5 读的就是这棵树 |
| 某节点长期「待测」 | 补题（首选），而不是把它并到隔壁节点。合并会让已经攒下的窗口数据失去含义 |

---

**相关文件**

- `server/content/curriculum/grade6-up.json` — 单元与诊断节点（`schema_version: 2`）
- `server/content/questions/{math,chinese,english}_6up.json` — 手写题库
- `server/content/taxonomy/pep-math-6up-v2.json` — 数学微观点快照
- `server/content/schema/{node,question,report}.schema.json` — 结构约束
- `server/tools/{check_content,import_curriculum,import_questions,reset_content}.py`
