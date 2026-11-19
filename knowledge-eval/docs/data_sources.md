# 数据来源与形态说明

> 面向两类读者：想知道「这些节点和题是从哪来的」的家长/老师，和想知道「文件和数据库到底谁说了算」的维护者。
> 最后核对：2026-11-19，对应 `knowledge-eval/data/` 当前内容。

---

## 1. 三科范围

| 科目 | 版本 | 聚合单元 | 诊断节点 | 题目 | 微观点来源 |
|---|---|---|---|---|---|
| 数学 | 人教版六年级上册（2022 课标） | 10 | 25 | 18 | `data/taxonomy/pep-math-6up-v2.json`，38 个微观点 / 55 条边 |
| 语文 | 统编版六年级上册（2026 秋现行修订版，8 单元 26 课） | 8 | 14 | 12 | 课文名，无公开分类法，`source_ids` 为空 |
| 英语 | 湘少版六年级上册（2026 秋新版，10 单元） | 10 | 16 | 16 | 单元语法点/话题，无公开分类法，`source_ids` 为空 |

聚合单元（`node_type = aggregate`）只负责报告分组，**不挂题、不判薄弱**；判薄弱的只有诊断节点。

---

## 2. 数学的微观点快照

- 文件：`data/taxonomy/pep-math-6up-v2.json`
- 来源：`github:xiaowen-pm/pep-primary-math-taxonomy`，路径 `data/6-up/{topics,dependencies}.json`
- 版本：`pep-primary-math-6up-v2`，`fetched_at` 2026-11-18
- 规模：38 个微观点、55 条依赖边（其中 29 条是同册内先修边）
- 运行时**不联网**：只有人重新切快照时才访问网络，服务端只读仓库里的这份副本。

每个微观点带 `type / domain / centrality / evidence / assessmentPrompt / standards`。节点的 `micro_nodes` 是人可读的名字，`source_ids` 是对应的 `mt_g6u_xxx`，两者等长同序。

**38/38 必须全覆盖**，这是 `check_content.py` 的反向校验：

```
python knowledge-eval/scripts/check_content.py
→ coverage pep-math-6up-v2.json: 38/38 micro points referenced
```

上一版正是漏在这里：`mt_g6u_036`「分数四则混合运算」的 `standards` 落在「6上.总复习.1」，按章节归并时不属于任何一章，被静默跳过；而校验只查「节点引用的 id 是否存在」，不查「快照里的 38 个是否都被引用」，所以漏了也不报错。现在它挂在 `math-6up-u-review`（总复习）下的 `math-6up-d25`，先修项 `mt_g6u_034`（节约用水的数据统计）。

---

## 3. 数学广角与综合与实践（更正）

旧文档写「数学广角、综合与实践不是独立章节，**已并入相应单元**」。这与数据不符，已更正：

- `math-6up-u08`「数学广角——数与形」是**独立聚合单元**，挂 1 个诊断节点（`math-6up-d22`，2 个微观点）。
- `math-6up-u09`「综合与实践」是**独立聚合单元**，挂 2 个诊断节点（`math-6up-d23` 确定起跑线、`math-6up-d24` 节约用水的数据）。
- `math-6up-u-review`「总复习」是第 10 个聚合单元，挂 1 个诊断节点。

教材把它们印成独立板块，账就对到独立单元上；把 023/024 塞进「圆」或「百分数」会让报告里的章节名和课本目录对不上。

---

## 4. 逐节点映射

### 数学（10 个聚合单元 / 25 个诊断节点）
| 诊断节点 | 名称 | 所属单元 | 覆盖的微观点 / 课文 | 知识类型 | 先修 ||---|---|---|---|---|---|| `math-6up-d01` | 分数乘整数 | 分数乘法 | 分数乘整数的意义、分数乘整数的计算 | procedural | — || `math-6up-d02` | 一个数乘分数与分数乘分数 | 分数乘法 | 一个数乘分数的意义、分数乘分数的计算 | procedural | math-6up-d01 || `math-6up-d03` | 分数乘法的运算律与应用题 | 分数乘法 | 分数乘法的运算律应用、分数乘法应用题 | procedural | math-6up-d02 || `math-6up-d04` | 积与因数的大小关系 | 分数乘法 | 乘积与被乘数大小的比较规律 | conceptual | math-6up-d02 || `math-6up-d05` | 用方向和距离确定位置 | 位置与方向（二） | 方向角表示位置、描述和绘制运动路线 | procedural | — || `math-6up-d06` | 倒数的认识 | 分数除法 | 倒数的认识 | conceptual | math-6up-d02 || `math-6up-d07` | 分数除法的计算 | 分数除法 | 分数除以整数的计算、一个数除以分数的计算 | procedural | math-6up-d06 || `math-6up-d08` | 商与被除数的关系 | 分数除法 | 商与被除数大小的关系 | conceptual | math-6up-d07、math-6up-d04 || `math-6up-d09` | 分数除法应用题 | 分数除法 | 分数除法应用题 | procedural | math-6up-d07 || `math-6up-d10` | 比的意义与基本性质 | 比 | 比的意义和各部分名称、比的基本性质与化简比 | procedural | math-6up-d07 || `math-6up-d11` | 按比分配 | 比 | 按比例分配 | procedural | math-6up-d10、math-6up-d02 || `math-6up-d12` | 比与分数、除法的联系 | 比 | 比与分数、除法的综合联系 | conceptual | math-6up-d13 || `math-6up-d13` | 圆的认识与对称性 | 圆 | 圆的认识：圆心、半径、直径、圆与对称性的联系 | procedural | math-6up-d15 || `math-6up-d14` | 圆周率与圆的周长 | 圆 | 圆周率π的认识、圆的周长公式与计算、刘徽割圆术的数学思想 | procedural | math-6up-d13、math-6up-d02、math-6up-d09 || `math-6up-d15` | 圆的面积 | 圆 | 圆的面积公式推导与计算 | procedural | math-6up-d14 || `math-6up-d16` | 组合图形与扇形 | 圆 | 圆形组合图形的面积、扇形的认识 | procedural | math-6up-d15、math-6up-d13 || `math-6up-d17` | 百分数的意义与取值范围 | 百分数（一） | 百分数的意义、百分数的取值范围 | conceptual | math-6up-d10、math-6up-d19 || `math-6up-d18` | 百分数与小数、分数的互化 | 百分数（一） | 百分数、小数、分数的互化、百分数的基本计算 | procedural | math-6up-d17 || `math-6up-d19` | 百分数增减的综合应用 | 百分数（一） | 百分数增减的综合应用 | procedural | math-6up-d18 || `math-6up-d20` | 扇形统计图的读图与制作 | 扇形统计图 | 扇形统计图的认识与制作 | representational | math-6up-d18、math-6up-d16 || `math-6up-d21` | 三种统计图的选择 | 扇形统计图 | 三种统计图的比较与选择 | conceptual | math-6up-d20 || `math-6up-d22` | 数与形结合的规律 | 数学广角——数与形 | 数形结合：奇数求和的图形规律、数形结合：无限递减分数的极限规律 | conceptual | — || `math-6up-d23` | 确定起跑线 | 综合与实践 | 确定起跑线的数学建模 | procedural | math-6up-d14 || `math-6up-d24` | 节约用水的数据 | 综合与实践 | 节约用水的数据统计 | procedural | math-6up-d18、math-6up-d20 || `math-6up-d25` | 分数四则混合运算 | 总复习 | 分数四则混合运算 | procedural | mt_g6u_034 |
### 语文（8 个聚合单元 / 14 个诊断节点）
| 诊断节点 | 名称 | 所属单元 | 覆盖的微观点 / 课文 | 知识类型 | 先修 ||---|---|---|---|---|---|| `chinese-6up-d01` | 写景状物类文本的语境词义与情感把握 | 触摸自然 | 草原、丁香结、古诗词三首 | 阅读理解 | — || `chinese-6up-d02` | 古诗词的背诵与理解（宿建德江·六月二十七日望湖楼醉书·西江月·夜行黄沙道中） | 触摸自然 | 宿建德江、六月二十七日望湖楼醉书、西江月·夜行黄沙道中 | 字词基础 | — || `chinese-6up-d03` | 革命题材叙事文的场面概括与人物品质 | 重温革命岁月 | 七律·长征、狼牙山五壮士、开国大典、我的战友邱少云 | 阅读理解 | — || `chinese-6up-d04` | 点面结合写法的辨识 | 重温革命岁月 | 开国大典、狼牙山五壮士 | 习作方法 | — || `chinese-6up-d05` | 按任务筛选信息与调整阅读速度 | 有目的地阅读 | 竹节人、宇宙生命之谜、故宫博物院 | 阅读理解 | — || `chinese-6up-d06` | 小说三要素的识别与分析 | 小说阅读 | 桥、穷人、金色的鱼钩 | 阅读理解 | — || `chinese-6up-d07` | 环境描写与人物心理的关系 | 小说阅读 | 穷人、桥 | 阅读理解 | — || `chinese-6up-d08` | 围绕中心意思选取材料 | 围绕中心意思写 | 灯光、盼、爸爸的计划、小站 | 习作方法 | — || `chinese-6up-d09` | 说明性文章的信息提取与观点依据 | 保护环境 | 只有一个地球、青山不老、三黑和土地 | 阅读理解 | — || `chinese-6up-d10` | 古诗三首的景物与情感（浪淘沙·江南春·书湖阴先生壁） | 保护环境 | 浪淘沙（其一）、江南春、书湖阴先生壁 | 字词基础 | — || `chinese-6up-d11` | 文言实词与文意（两小儿辩日·曹冲称象） | 科学与想象 | 两小儿辩日、曹冲称象 | 字词基础 | — || `chinese-6up-d12` | 科学类文本的想象与推理 | 科学与想象 | 表里的生物、他们那时候多有趣啊 | 阅读理解 | — || `chinese-6up-d13` | 鲁迅作品的人物形象与时代背景 | 走近鲁迅 | 少年闰土、好的故事、我的伯父鲁迅先生、有的人——纪念鲁迅有感 | 阅读理解 | — || `chinese-6up-d14` | 借助资料理解课文 | 走近鲁迅 | 少年闰土、我的伯父鲁迅先生 | 阅读理解 | — |
### 英语（10 个聚合单元 / 16 个诊断节点）
| 诊断节点 | 名称 | 所属单元 | 覆盖的微观点 / 课文 | 知识类型 | 先修 ||---|---|---|---|---|---|| `english-6up-d01` | 一般过去时（规则与不规则动词）+ 谈假期活动 | What did you do during the holidays? | 一般过去时、不规则动词过去式、What did you do during the holidays? | grammar | — || `english-6up-d02` | 假期活动词汇与问答 | What did you do during the holidays? | 假期活动动词、城市与家乡名称 | vocabulary | — || `english-6up-d03` | 一般现在时第三人称单数 | Beibei always gets up early | 一般现在时、第三人称单数、Beibei always gets up early | grammar | — || `english-6up-d04` | 频度副词 | Beibei always gets up early | 频度副词、日常作息表达 | vocabulary | — || `english-6up-d06` | enjoy + doing（节日活动） | The Mid-Autumn Festival | enjoy doing、like / love + doing、enjoy eating mooncakes | grammar | — || `english-6up-d07` | 一般现在时与一般过去时在节日描述中的对比 | The Mid-Autumn Festival | 一般现在时、一般过去时、节日活动表达 | grammar | — || `english-6up-d08` | 一般将来时 will（含 There will be） | It will be sunny and cool tomorrow | 一般将来时 will、There will be 句型、天气描述 | grammar | — || `english-6up-d09` | 天气词汇与活动安排 | It will be sunny and cool tomorrow | 天气形容词、天气下的活动 | vocabulary | — || `english-6up-d10` | 量词表达（a bottle of / a glass of） | I will bring a bottle of orange juice | 量词 a bottle of、a glass of / a cup of、不可数名词 | vocabulary | — || `english-6up-d11` | 情态动词 can 表能力与请求 | What can I do? | 情态动词 can、提供帮助的表达 | grammar | — || `english-6up-d12` | should / shouldn't 表建议（环保） | We shouldn't waste water | 情态动词 should、3R 环保表达 | grammar | — || `english-6up-d13` | 形容词比较级（-er / more） | This bird is bigger than the first one | 形容词比较级、双写辅音、more + 长形容词 | grammar | — || `english-6up-d14` | 比较级句型 than | This bird is bigger than the first one | 比较级句型 than、两者比较 | sentence_pattern | — || `english-6up-d15` | 节日习俗表达与最高级 | Festivals bring families together | 节日习俗、形容词最高级 | vocabulary | — || `english-6up-d16` | 疾病与身体不适表达 | I don't feel well | What's wrong with you?、have a cold / fever / headache、see a doctor / take medicine | vocabulary | — || `english-6up-d17` | should / shouldn't 表建议（看病） | I don't feel well | should + 动词原形、给建议、劝阻 | grammar | — |

---

## 5. 校验都查什么

`scripts/check_content.py`，六类问题，任一条命中即以非零码退出：

1. **schema**：原始对象逐字段校验（`node.schema.json` / `question.schema.json`），没有字段改名、没有数组重塑；
2. **图结构**：`node_id` 唯一；诊断节点的 `parent_id` 必须是一个聚合节点；聚合节点的 `parent_id` 必须为空、不得带 `knowledge_type`、不得声明先修；
3. **先修**：`prerequisite_node_ids` 里每一项要么是存在的诊断节点，要么是快照里的 `mt_g6u_xxx`；
4. **覆盖（正向）**：节点引用的每个 `source_id` 都在快照里；
5. **覆盖（反向）**：快照的 id 集合与节点引用集合必须相等，差集非空即失败，并打印缺失/多余清单。两边数量都从文件读，**没有 `== 38` 这类硬编码**；
6. **题目**：`diagnostic_node_id` 必须是诊断节点；选择题的 `correct_answer` 必须是 `A/B/C/D` 且在 `options` 范围内；填空题只能出现在数学（电视遥控器没有字母键）、`accepted[0]` 必须等于 `correct_answer`、且能解析成数字或分数；每个被覆盖的节点至少两种 `ability_tag`、至少一道「中」题。

---

## 6. 形态说明：文件与数据库的关系

**文件是源，数据库是运行时的副本。**

- `knowledge-eval/data/*.json` 是唯一手工维护的源；`server/tools/import_curriculum.py` 与 `import_questions.py` 直接读它，不做字段翻译。
- `unit.code` ← `node_id`（聚合），`topic.code` ← `node_id`（诊断），`topic.chapter_order` ← `chapter_order`，`topic.source_ids_json` ← `source_ids`。
- `question.unit_id` 为空：题目只挂诊断节点。出题排序用的章节顺序来自 `topic.chapter_order`，`picker` 不再 join `unit`。
- `export_from_db.py --check` 把库里的内容重新导成同一形状，与仓库文件逐字段比对；两边一致才说明「文件与库没有分叉」。

**evaluate.py 与 report.py 的分工**：`report.py` 决定报告长什么样（字段、档位文案、薄弱项过滤），`evaluate.py` 只负责把「作答文件」变成 `report.build` 需要的行。两者都调用 `mastery.summarise` / `mastery.level_of`，模型只有一份实现。

**weakness 的 resolved 策略**：节点回到「2-熟练」及以上时，把 `weakness.resolved` 置 1 而**不删除**。理由：家长问「上个月分数除法是不是薄弱」时，行还在；删除会让这个问题永远答「没有这个节点」。复发时清回 0，`opened_at` 保留第一次的时间。

**负样本怎么跑**：

```
python -m pytest knowledge-eval/tests/ -v
```

`test_check_content_negative.py` 故意删掉 `mt_g6u_036` 的引用、再加一个不存在的 `mt_g6u_999`，断言脚本**失败并点名**；`test_schema_direct.py` 删 `node_type`、把 `micro_nodes` 改成对象数组，断言 schema **失败**，并断言校验器收到的是文件里的原始 dict；`test_report_weak_only.py` 断言默认报告里不出现 `2-熟练`/`3-拓展`，以及 `--include-full-view` 时它们进 `full_view` 而不进 `diagnostics`。

---

## 7. 什么时候要更新

| 触发 | 动作 |
|---|---|
| 教材换版（英语出新版、语文修订、数学换新教材） | 改 `data/nodes_*.json` 与 `data/questions_*.json`，跑 `check_schema` + `check_content` + `check_db_parity`，再 `import_curriculum` 重导。**目录必须以孩子手上的课本为准**，网络上的"2026 版"页面大量是旧版挂新名 |
| 数学分类法更新 | 换 `data/taxonomy/*.json`，跑 `check_content`；反向校验会立刻告诉你哪些微观点没人管 |
| 新增题目 | 加到对应 `questions_*.json`，`check_content` 会检查节点存在、题型合法、每节点至少两种能力线 |
| 生产库结构升级 | 先备份，再跑 `server/db/migrate-2026-11-19-flat-nodes.sql`（只加不删，只能跑一次） |
