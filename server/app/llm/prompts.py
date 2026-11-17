"""Prompts, kept in one file so a change to the wording is one diff and not a hunt.

Everything here is written for a sixth-grader's parent, not for a teacher: short stems, one right
answer, and an explanation that says *why* rather than restating the question. The hard limits come
from `docs/QUIZ.md` §6 and are repeated in the prompt because a model that has not been told them will
happily produce a 300-character word problem:

  * stem ≤ 120 characters;
  * only `choice` (four options, exactly one correct) and `fill` (a number or a short word);
  * no figures, no charts, no listening, no reading passage;
  * one difficulty (1–3) and one angle (`recall` / `understand` / `apply`) per item.

The `check_expr` / `check_value` pair is the part that makes machine verification possible at all. We
never ask the model to *describe* its arithmetic and then try to parse the description; we ask for the
expression and the value, and `sympy` recomputes one from the other. A model that cannot fill those in
has produced something we cannot check, which for a maths question means we do not want it.
"""

from __future__ import annotations

SUBJECT_HINTS = {
    "math": "数学。数字与算式必须是整数或有限小数/最简分数，不要出现无法精确表示的结果。",
    "chinese": "语文。涉及古诗、课文、文学常识时，答案必须是课本里有出处的确定事实，不要考观点题。",
    "english": "英语。题干以中文说明、以英文作答为宜；不要考听力，不要出现图片。",
}

SYSTEM = """你是一位六年级学科老师，为家庭电视答题系统出题。规则（违反任何一条这道题就会被丢弃）：

1. 题干不超过 120 个汉字，一句话说清，不铺垫、不讲故事。
2. 题型只有两种：choice（四选一，恰好一个正确）或 fill（填一个数字或一个短词）。
3. 不出图、不出图表、不出听力、不出超过 400 字的阅读。
4. 答案唯一，且能由题干唯一确定；不要把有争议的说法当正确答案。
5. 选择题的四个选项互不相同、长度相近，错误选项要是"像样的错"（常见算错、概念混淆），不能是明显的胡话。
6. 题干里不能出现答案本身。
7. 解析写给家长：一句话说清为什么，不超过 120 个汉字，不要重复题干。
8. 数学题必须给出 check_expr（只含数字与 + - * / ( ) 的算式）和 check_value（该算式的值），
   两者必须真的相等，系统会用 sympy 重新算一遍。
9. 语文、英语题必须给出 source_ref（课文篇目 / 单元 / 词表条目），没有出处的事实不要出。
10. 只输出 JSON，不要输出任何解释、前后缀或 Markdown 代码围栏。"""


def question_messages(
    *,
    subject: str,
    unit: str,
    topic: str,
    count: int,
    difficulty: int = 2,
    angle: str = "understand",
    qtype: str = "choice",
    avoid: list[str] | None = None,
) -> list[dict]:
    """Ask for `count` questions on one topic.

    `avoid` carries the stems already in the bank for this topic. It is not a substitute for the
    de-duplication key — nothing a model says about novelty can be trusted — but it stops the obvious
    case where a batch of twelve contains the same question eleven times.
    """
    hint = SUBJECT_HINTS.get(subject, "")
    avoid_block = ""
    if avoid:
        listed = "\n".join(f"- {stem}" for stem in avoid[:20])
        avoid_block = f"\n下面这些题干库里已经有了，不要重复，也不要只改数字：\n{listed}\n"

    user = f"""请为下面这个知识点出 {count} 道题。

学科：{subject}（{hint}）
单元：{unit}
知识点：{topic}
难度：{difficulty}（1 简单 / 2 中等 / 3 较难）
考察角度：{angle}（recall 识记 / understand 理解 / apply 应用）
题型：{qtype}
{avoid_block}
输出一个 json 对象，结构必须完全如下（不要多字段、不要少字段）：

{{
  "items": [
    {{
      "stem": "题干",
      "qtype": "{qtype}",
      "options": [{{"label": "A", "text": "..."}}, {{"label": "B", "text": "..."}},
                  {{"label": "C", "text": "..."}}, {{"label": "D", "text": "..."}}],
      "correct_label": "A",
      "answer": "",
      "accepted": [],
      "cmp_kind": "exact",
      "difficulty": {difficulty},
      "angle": "{angle}",
      "explanation": "解析",
      "check_expr": "",
      "check_value": "",
      "source_ref": ""
    }}
  ]
}}

如果是 fill 题：options 给空数组，correct_label 给空字符串，answer 填规范答案，
accepted 填等价写法（如 0.75 的 "3/4"），cmp_kind 填 numeric 或 text。
如果是语文或英语题：check_expr 与 check_value 都填空字符串，source_ref 必须填。
"""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


#: The cheapest possible round trip. Used by `python -m server.app.llm ping` to prove that the key was
#: injected and that the endpoint answers, without spending money on a real generation.
PING = [
    {"role": "system", "content": "只回答一个字，不要标点。"},
    {"role": "user", "content": "回一个字：好"},
]
