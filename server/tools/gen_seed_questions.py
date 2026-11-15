"""Generate the seed question bank: small, offline, and every maths answer re-derived.

P1 needs enough questions to walk a quiz from "serve" to "ticket", not a real bank. So this is a
*template* generator, not an AI caller:

  * each topic in the curriculum names a template (`generator` in grade6-up.json);
  * a template turns sampled parameters into one question — same shape, different numbers;
  * maths answers are recomputed by sympy from the expression itself before the question is stored
    (`verify_status='sympy_ok'`), which is the thin end of E6;
  * Chinese and English questions come from small curated tables (poem lines, irregular verbs). Those
    are facts a human wrote down; they are marked `manual_ok` and are **not** sympy-checked, because
    there is nothing numeric to check.

Why templates rather than a language model: a template cannot hallucinate a wrong answer, and it gives
the de-duplication in `question(param_hash)` something real to key on. When the real bank is built
later (a separate decision), whatever generates it should write the same rows; this file is then only
a fixture.

    python3 -m server.tools.gen_seed_questions --per-topic 5

Deterministic: the RNG is seeded per (topic, slot), so re-running adds nothing and changes nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from server.app.db import Database, default_db  # noqa: E402

try:
    import sympy as sp
except ImportError:  # pragma: no cover - the maths check is mandatory, so fail loudly
    print("sympy is required: the maths answers must be re-derived before they are stored", file=sys.stderr)
    raise

#: Anything outside this is not arithmetic and must never reach sympy. `sympify` walks into eval-like
#: territory, so the expression we hand it is built here from digits and operators, never from input.
EXPR_SAFE = set("0123456789+-*/(). ")


# ---------------------------------------------------------------- helpers

def fmt(value: Fraction) -> str:
    """Textbook form: integer, up to two decimals when it terminates, otherwise a plain fraction."""
    if value.denominator == 1:
        return str(value.numerator)
    decimal = value * 100
    if decimal.denominator == 1:          # terminates within two places, e.g. 3/4 -> 0.75
        return f"{float(value):.2f}".rstrip("0").rstrip(".")
    return f"{value.numerator}/{value.denominator}"


def equivalents(value: Fraction) -> list[tuple[str, str]]:
    """Every accepted spelling of one answer.

    Both the fraction and the decimal are always listed. An earlier version derived the list from
    `fmt()`, which already turns 3/4 into 0.75 — so a child who wrote 3/4 was marked wrong for giving
    the same number in the form the textbook taught first. The point of this table is that it does not
    depend on which spelling was chosen for display.
    """
    spellings: set[str] = set()
    if value.denominator == 1:
        spellings.add(str(value.numerator))
        spellings.add(f"{value.numerator}.0")
    else:
        spellings.add(f"{value.numerator}/{value.denominator}")
        if (value * 100).denominator == 1:
            spellings.add(f"{float(value):.2f}".rstrip("0").rstrip("."))
    return [(text, "numeric") for text in sorted(spellings)]


def check_math(expression: str, expected: Fraction) -> tuple[bool, str]:
    """Re-derive the answer from the expression. The one gate every maths question must pass."""
    if set(expression) - EXPR_SAFE:
        return False, "expression contains characters outside arithmetic"
    try:
        value = sp.sympify(expression, evaluate=True)
    except Exception as exc:  # sympify raises a zoo of exception types
        return False, f"sympify failed: {exc}"
    try:
        difference = abs(float(value) - float(expected))
    except (TypeError, ValueError):
        return False, "expression is not numeric"
    if difference > 1e-9:
        return False, f"re-derived {value}, stored {expected}"
    return True, f"sympy: {expression} = {fmt(expected)}"


def proper_fraction(rng: random.Random, denominators: list[int]) -> tuple[int, int]:
    """A fraction already in lowest terms. 6/9 in a stem reads wrong to a child and to a teacher,
    and it makes "约分" look like something the question forgot to do."""
    b = rng.choice(denominators)
    a = rng.randint(1, b - 1)
    common = math.gcd(a, b)
    return a // common, b // common


def param_hash(params: dict) -> str:
    payload = json.dumps(params, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def mcq(stem: str, correct: str, distractors: list[str], rng: random.Random) -> tuple[list[tuple[str, str, bool]], str]:
    """Four options, shuffled, labels A..D. Returns (options, correct label).

    Duplicates and any distractor equal to the answer are dropped: two identical options would make a
    question ungradeable, and a distractor that is secretly right would fail a child who picked it.
    """
    unique: list[str] = []
    for text in distractors:
        if text and text != correct and text not in unique:
            unique.append(text)
    while len(unique) < 3:
        # Last resort so a template with a thin distract (e.g. 3 spelling variants) still yields four
        # options. Deliberately implausible; a real bank should never reach this line.
        unique.append(f"{correct}?{len(unique)}")
    choices = [(correct, True)] + [(text, False) for text in unique[:3]]
    rng.shuffle(choices)
    labels = "ABCD"
    options = [(labels[i], text, is_correct) for i, (text, is_correct) in enumerate(choices)]
    correct_label = next(label for label, _, is_correct in options if is_correct)
    return options, correct_label


# ---------------------------------------------------------------- maths templates

def frac_mul_int(rng: random.Random) -> dict:
    a, b = proper_fraction(rng, [3, 4, 5, 6, 8, 9])
    n = rng.randint(2, 9)
    expected = Fraction(a, b) * n
    return {
        "qtype": "fill",
        "stem": f"{a}/{b} × {n} = （   ）",
        "answer": fmt(expected),
        "explanation": f"分数乘整数，分子与整数相乘：{a}×{n}={a * n}，得 {a * n}/{b}"
                       + (f"，约分后是 {fmt(expected)}" if expected.denominator != b else ""),
        "difficulty": 1,
        "angle": "recall",
        "expr": f"({a}/{b})*{n}",
        "expected": expected,
        "params": {"a": a, "b": b, "n": n},
        "template": "frac_mul_int",
    }


def frac_mul_frac(rng: random.Random) -> dict:
    a, b = proper_fraction(rng, [3, 4, 5, 6, 7, 8])
    c, d = proper_fraction(rng, [3, 4, 5, 6, 9])
    expected = Fraction(a, b) * Fraction(c, d)
    return {
        "qtype": "fill",
        "stem": f"{a}/{b} × {c}/{d} = （   ）",
        "answer": fmt(expected),
        "explanation": f"分子相乘作分子、分母相乘作分母：{a}×{c}={a * c}，{b}×{d}={b * d}，约分得 {fmt(expected)}",
        "difficulty": 2,
        "angle": "recall",
        "expr": f"({a}/{b})*({c}/{d})",
        "expected": expected,
        "params": {"a": a, "b": b, "c": c, "d": d},
        "template": "frac_mul_frac",
    }


def frac_mul_word(rng: random.Random) -> dict:
    a, b = proper_fraction(rng, [3, 4, 5, 8])
    n = rng.choice([12, 16, 20, 24, 30, 40, 60])
    expected = Fraction(n) * Fraction(a, b)
    unit = rng.choice(["米", "千克", "升"])
    return {
        "qtype": "fill",
        "stem": f"一根绳子长 {n} {unit}，用去了它的 {a}/{b}，用去了多少{unit}？（只填数字）",
        "answer": fmt(expected),
        "explanation": f"求 {n} 的 {a}/{b}，用乘法：{n}×{a}/{b}={fmt(expected)}",
        "difficulty": 3,
        "angle": "apply",
        "expr": f"{n}*({a}/{b})",
        "expected": expected,
        "params": {"a": a, "b": b, "n": n},
        "template": "frac_mul_word",
    }


def reciprocal(rng: random.Random) -> dict:
    a, b = proper_fraction(rng, [2, 3, 4, 5, 6, 7, 8, 9])
    expected = Fraction(b, a)
    return {
        "qtype": "fill",
        "stem": f"{a}/{b} 的倒数是（   ）",
        "answer": fmt(expected),
        "explanation": f"把分子分母交换位置，{a}/{b} 的倒数是 {b}/{a}",
        "difficulty": 1,
        "angle": "recall",
        "expr": f"1/({a}/{b})",
        "expected": expected,
        "params": {"a": a, "b": b},
        "template": "reciprocal",
    }


def frac_div_int(rng: random.Random) -> dict:
    a, b = proper_fraction(rng, [3, 4, 5, 6, 7])
    n = rng.randint(2, 6)
    expected = Fraction(a, b) / n
    return {
        "qtype": "fill",
        "stem": f"{a}/{b} ÷ {n} = （   ）",
        "answer": fmt(expected),
        "explanation": f"除以整数等于乘这个整数的倒数：{a}/{b}×1/{n}={fmt(expected)}",
        "difficulty": 2,
        "angle": "recall",
        "expr": f"({a}/{b})/{n}",
        "expected": expected,
        "params": {"a": a, "b": b, "n": n},
        "template": "frac_div_int",
    }


def frac_div_frac(rng: random.Random) -> dict:
    a, b = proper_fraction(rng, [3, 4, 5, 6, 8])
    c, d = proper_fraction(rng, [3, 5, 7, 9])
    expected = Fraction(a, b) / Fraction(c, d)
    return {
        "qtype": "fill",
        "stem": f"{a}/{b} ÷ {c}/{d} = （   ）",
        "answer": fmt(expected),
        "explanation": f"除以一个分数等于乘它的倒数：{a}/{b}×{d}/{c}={fmt(expected)}",
        "difficulty": 3,
        "angle": "understand",
        "expr": f"({a}/{b})/({c}/{d})",
        "expected": expected,
        "params": {"a": a, "b": b, "c": c, "d": d},
        "template": "frac_div_frac",
    }


def ratio_simplify(rng: random.Random) -> dict:
    a, b = rng.choice([(2, 3), (3, 4), (2, 5), (3, 5), (4, 5), (5, 6), (1, 3)])
    m = rng.randint(2, 9)
    expected_text = f"{a}:{b}"
    return {
        "qtype": "fill",
        "stem": f"把 {a * m}:{b * m} 化成最简整数比是（   ）（形如 a:b）",
        "answer": expected_text,
        "explanation": f"{a * m} 和 {b * m} 同时除以 {m}，得到 {expected_text}",
        "difficulty": 2,
        "angle": "understand",
        "expr": None,
        "expected": None,
        "cmp": "text",
        "params": {"a": a, "b": b, "m": m},
        "template": "ratio_simplify",
    }


def ratio_split(rng: random.Random) -> dict:
    a, b = rng.choice([(1, 2), (2, 3), (1, 3), (3, 4), (2, 5)])
    unit_total = a + b
    k = rng.randint(3, 12)
    total = unit_total * k
    expected = Fraction(a * k)
    return {
        "qtype": "fill",
        "stem": f"把 {total} 本书按 {a}:{b} 分给两个班，较少的一份是（   ）本。",
        "answer": fmt(expected),
        "explanation": f"总份数 {a}+{b}={unit_total}，每份 {total}÷{unit_total}={k}，较少的一份是 {a}×{k}={a * k}",
        "difficulty": 3,
        "angle": "apply",
        "expr": f"{total}*{a}/({a}+{b})",
        "expected": expected,
        "params": {"a": a, "b": b, "total": total},
        "template": "ratio_split",
    }


def circle_radius_diameter(rng: random.Random) -> dict:
    r = rng.randint(2, 12)
    ask_diameter = rng.random() < 0.5
    if ask_diameter:
        expected, stem = Fraction(2 * r), f"一个圆的半径是 {r} 厘米，它的直径是（   ）厘米。"
        explanation = f"同圆内直径是半径的 2 倍：{r}×2={2 * r}"
        expr = f"{r}*2"
    else:
        d = 2 * r
        expected, stem = Fraction(r), f"一个圆的直径是 {d} 厘米，它的半径是（   ）厘米。"
        explanation = f"半径是直径的一半：{d}÷2={r}"
        expr = f"{d}/2"
    return {
        "qtype": "fill", "stem": stem, "answer": fmt(expected), "explanation": explanation,
        "difficulty": 1, "angle": "recall", "expr": expr, "expected": expected,
        "params": {"r": r, "ask": "d" if ask_diameter else "r"}, "template": "circle_radius_diameter",
    }


def circle_circumference(rng: random.Random) -> dict:
    r = rng.randint(2, 10)
    expected = Fraction(314, 100) * 2 * r
    return {
        "qtype": "fill",
        "stem": f"一个圆的半径是 {r} 厘米，它的周长是（   ）厘米。（π 取 3.14）",
        "answer": fmt(expected),
        "explanation": f"C=2πr=2×3.14×{r}={fmt(expected)}",
        "difficulty": 2, "angle": "apply", "expr": f"2*3.14*{r}", "expected": expected,
        "params": {"r": r}, "template": "circle_circumference",
    }


def circle_area(rng: random.Random) -> dict:
    r = rng.randint(2, 10)
    expected = Fraction(314, 100) * r * r
    return {
        "qtype": "fill",
        "stem": f"一个圆的半径是 {r} 厘米，它的面积是（   ）平方厘米。（π 取 3.14）",
        "answer": fmt(expected),
        "explanation": f"S=πr²=3.14×{r}²={fmt(expected)}",
        "difficulty": 3, "angle": "apply", "expr": f"3.14*{r}*{r}", "expected": expected,
        "params": {"r": r}, "template": "circle_area",
    }


def percent_to_percent(rng: random.Random) -> dict:
    a, b = proper_fraction(rng, [2, 4, 5, 10, 20, 25, 50])
    expected = Fraction(a * 100, b)
    return {
        "qtype": "fill",
        "stem": f"把 {a}/{b} 化成百分数是（   ）%。（只填数字）",
        "answer": fmt(expected),
        "explanation": f"{a}/{b}={float(Fraction(a, b)):.2f}，再乘 100 得 {fmt(expected)}%",
        "difficulty": 1, "angle": "understand", "expr": f"({a}/{b})*100", "expected": expected,
        "params": {"a": a, "b": b}, "template": "percent_to_percent",
    }


def percent_of(rng: random.Random) -> dict:
    n = rng.choice([40, 50, 60, 80, 120, 200, 300])
    p = rng.choice([10, 20, 25, 30, 40, 50, 75])
    expected = Fraction(n * p, 100)
    return {
        "qtype": "fill",
        "stem": f"{n} 的 {p}% 是（   ）",
        "answer": fmt(expected),
        "explanation": f"求一个数的百分之几用乘法：{n}×{p}%={n}×{p}/100={fmt(expected)}",
        "difficulty": 2, "angle": "apply", "expr": f"{n}*{p}/100", "expected": expected,
        "params": {"n": n, "p": p}, "template": "percent_of",
    }


# ---------------------------------------------------------------- curated banks

#: 六年级上册课内古诗的上下句。Curated, not generated: a wrong poem line is the kind of error that
#: makes a child distrust the whole exercise. Each entry: (上句, 下句, 出处).
POEM_LINES = [
    ("移舟泊烟渚", "日暮客愁新", "《宿建德江》孟浩然"),
    ("野旷天低树", "江清月近人", "《宿建德江》孟浩然"),
    ("黑云翻墨未遮山", "白雨跳珠乱入船", "《六月二十七日望湖楼醉书》苏轼"),
    ("卷地风来忽吹散", "望湖楼下水如天", "《六月二十七日望湖楼醉书》苏轼"),
    ("明月别枝惊鹊", "清风半夜鸣蝉", "《西江月·夜行黄沙道中》辛弃疾"),
    ("稻花香里说丰年", "听取蛙声一片", "《西江月·夜行黄沙道中》辛弃疾"),
    ("红军不怕远征难", "万水千山只等闲", "《七律·长征》毛泽东"),
    ("五岭逶迤腾细浪", "乌蒙磅礴走泥丸", "《七律·长征》毛泽东"),
    ("金沙水拍云崖暖", "大渡桥横铁索寒", "《七律·长征》毛泽东"),
    ("更喜岷山千里雪", "三军过后尽开颜", "《七律·长征》毛泽东"),
    ("故人具鸡黍", "邀我至田家", "《过故人庄》孟浩然"),
    ("绿树村边合", "青山郭外斜", "《过故人庄》孟浩然"),
    ("胜日寻芳泗水滨", "无边光景一时新", "《春日》朱熹"),
    ("等闲识得东风面", "万紫千红总是春", "《春日》朱熹"),
]

#: verb -> (third person singular, past tense). Curated: irregular forms are exactly where a generated
#: table goes wrong, and a wrong form teaches the child something false.
VERBS = {
    "go": ("goes", "went"), "watch": ("watches", "watched"), "study": ("studies", "studied"),
    "have": ("has", "had"), "do": ("does", "did"), "fly": ("flies", "flew"),
    "play": ("plays", "played"), "wash": ("washes", "washed"), "see": ("sees", "saw"),
    "eat": ("eats", "ate"), "take": ("takes", "took"), "buy": ("buys", "bought"),
    "read": ("reads", "read"), "swim": ("swims", "swam"),
}

#: adjective -> (comparative, a wrong form a child plausibly writes). Irregular forms and the
#: double-the-consonant rule are exactly where a generated table goes wrong.
ADJECTIVES = {
    "big": ("bigger", "biger"),
    "tall": ("taller", "more tall"),
    "happy": ("happier", "happyer"),
    "hot": ("hotter", "hoter"),
    "easy": ("easier", "easyer"),
    "good": ("better", "gooder"),
    "small": ("smaller", "more small"),
    "thin": ("thinner", "thiner"),
}


def poem_jielong(rng: random.Random) -> dict:
    upper, lower, source = rng.choice(POEM_LINES)
    others = [line for up, line, _ in POEM_LINES if line != lower]
    rng.shuffle(others)
    options, correct = mcq(f"「{upper}」的下一句是：", lower, others, rng)
    return {
        "qtype": "choice", "stem": f"「{upper}」的下一句是（   ）",
        "options": options, "answer": correct,
        "explanation": f"{upper}，{lower}。——{source}",
        "difficulty": 1, "angle": "recall", "expr": None, "expected": None,
        "params": {"upper": upper}, "template": "poem_jielong",
    }


def past_simple(rng: random.Random) -> dict:
    verb = rng.choice(list(VERBS))
    _, past = VERBS[verb]
    # Wrong forms must be wrong *forms of this verb*, not wrong words: "doed" in a question about "fly"
    # tells a child nothing except that the question was machine-made.
    regular = verb + "ed"
    y_rule = (verb[:-1] + "ied") if verb.endswith("y") else verb + "d"
    wrong = [regular, y_rule, verb]
    options, correct = mcq(f"I ___ to Beijing last summer.（用 {verb} 的正确形式填空）", past, wrong, rng)
    return {
        "qtype": "choice", "stem": f"I ___ to Beijing last summer.（用 {verb} 的正确形式填空）",
        "options": options, "answer": correct,
        "explanation": f"last summer 表示过去，用一般过去时：{verb} → {past}",
        "difficulty": 2, "angle": "apply", "expr": None, "expected": None,
        "params": {"verb": verb}, "template": "past_simple",
    }


def third_person_singular(rng: random.Random) -> dict:
    verb = rng.choice(list(VERBS))
    third, _ = VERBS[verb]
    # The three wrong forms a child actually writes: no ending at all, a bare -s on a verb that needs
    # -es, and the -y rule applied where it does not belong.
    wrong = [verb, verb + "s", (verb[:-1] + "ies") if verb.endswith("y") else verb + "es"]
    options, correct = mcq(f"She ___ to school every day.（用 {verb} 的正确形式）", third, wrong, rng)
    return {
        "qtype": "choice", "stem": f"She ___ to school every day.（用 {verb} 的正确形式）",
        "options": options, "answer": correct,
        "explanation": f"主语 She 是第三人称单数，一般现在时动词加 -s/-es：{verb} → {third}",
        "difficulty": 2, "angle": "recall", "expr": None, "expected": None,
        "params": {"verb": verb}, "template": "third_person_singular",
    }


def comparative(rng: random.Random) -> dict:
    adjective = rng.choice(list(ADJECTIVES))
    correct_form, plausible_wrong = ADJECTIVES[adjective]
    wrong = [plausible_wrong, adjective + "er", f"more {adjective}"]
    options, correct = mcq(f"{adjective} 的比较级是（   ）", correct_form, wrong, rng)
    return {
        "qtype": "choice", "stem": f"{adjective} 的比较级是（   ）",
        "options": options, "answer": correct,
        "explanation": f"{adjective} → {correct_form}（一般加 -er；重读闭音节双写末尾字母；辅音字母+y 变 i 加 -er；不规则形式单独记）",
        "difficulty": 2, "angle": "recall", "expr": None, "expected": None,
        "params": {"adjective": adjective}, "template": "comparative",
    }


#: (adjective, subject, comparison, correct form). Written out rather than derived: the point of the
#: question is the irregular or doubled form, and deriving it is how a template gets it wrong.
COMPARISONS = [
    ("big", "This bird", "the first one", "bigger"),
    ("tall", "Tom", "Jim", "taller"),
    ("hot", "July", "June", "hotter"),
    ("easy", "This book", "that one", "easier"),
    ("good", "This film", "that one", "better"),
    ("thin", "This book", "that one", "thinner"),
]


def comparative_than(rng: random.Random) -> dict:
    adjective, subject, other, correct_form = rng.choice(COMPARISONS)
    wrong = [adjective, adjective + "er", adjective + "est", f"more {adjective}"]
    options, correct = mcq(f"{subject} is ___ than {other}.", correct_form, wrong, rng)
    return {
        "qtype": "choice", "stem": f"{subject} is ___ than {other}.（用 {adjective} 的正确形式）",
        "options": options, "answer": correct,
        "explanation": f"than 前面用比较级：{adjective} → {correct_form}",
        "difficulty": 2, "angle": "apply", "expr": None, "expected": None,
        "params": {"adjective": adjective, "subject": subject}, "template": "comparative_than",
    }


TEMPLATES = {
    "frac_mul_int": frac_mul_int,
    "frac_mul_frac": frac_mul_frac,
    "frac_mul_word": frac_mul_word,
    "reciprocal": reciprocal,
    "frac_div_int": frac_div_int,
    "frac_div_frac": frac_div_frac,
    "ratio_simplify": ratio_simplify,
    "ratio_split": ratio_split,
    "circle_radius_diameter": circle_radius_diameter,
    "circle_circumference": circle_circumference,
    "circle_area": circle_area,
    "percent_of": percent_of,
    "percent_to_percent": percent_to_percent,
    "poem_jielong": poem_jielong,
    "past_simple": past_simple,
    "third_person_singular": third_person_singular,
    "comparative": comparative,
    "comparative_than": comparative_than,
}

#: Templates whose answers are numbers and must survive a sympy re-derivation.
MATH_TEMPLATES = {
    "frac_mul_int", "frac_mul_frac", "frac_mul_word", "reciprocal", "frac_div_int",
    "frac_div_frac", "ratio_split", "circle_radius_diameter", "circle_circumference",
    "circle_area", "percent_of", "percent_to_percent",
}


# ---------------------------------------------------------------- database

def upsert_question(db: Database, *, topic_id: int, textbook_id: int, unit_id: int, record: dict) -> str:
    """Insert unless the same (topic, template, parameters) is already there. Returns a status word."""
    digest = param_hash(record["params"])
    existing = db.query_one(
        "SELECT id FROM question WHERE topic_id = ? AND template_key = ? AND param_hash = ?",
        (topic_id, record["template"], digest),
    )
    if existing is not None:
        return "skipped"

    is_math = record["template"] in MATH_TEMPLATES
    if is_math:
        ok, note = check_math(record["expr"], record["expected"])
        if not ok:
            return f"rejected ({note})"
        verify_status = "sympy_ok"
    else:
        note = f"curated bank: {record['template']}"
        verify_status = "manual_ok"

    stem = record["stem"]
    with db.tx() as cur:
        question_id = cur.insert(
            "INSERT INTO question (textbook_id, unit_id, topic_id, angle, difficulty, qtype, stem,"
            " stem_len, answer_norm, explanation_zh, verify_status, verify_note, source,"
            " template_key, param_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ai', ?, ?)",
            (
                textbook_id, unit_id, topic_id, record["angle"], record["difficulty"], record["qtype"],
                stem, len(stem), record["answer"], record["explanation"], verify_status, note,
                record["template"], digest,
            ),
        )
        if record["qtype"] == "choice":
            for label, text, is_correct in record["options"]:
                cur.insert(
                    "INSERT INTO question_option (question_id, label, text, is_correct) VALUES (?, ?, ?, ?)",
                    (question_id, label, text, 1 if is_correct else 0),
                )
        else:
            cmp_kind = record.get("cmp", "numeric")
            spellings = equivalents(record["expected"]) if record["expected"] is not None else [(record["answer"], "text")]
            for ordinal, (text, kind) in enumerate(spellings):
                cur.insert(
                    "INSERT INTO question_answer (question_id, ordinal, text_raw, cmp_kind) VALUES (?, ?, ?, ?)",
                    (question_id, ordinal, text, cmp_kind or kind),
                )
    return "created"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the P1 seed question bank.")
    parser.add_argument("--per-topic", type=int, default=5, help="questions per topic (default 5)")
    parser.add_argument("--dsn", default=None)
    args = parser.parse_args(argv)

    db = Database(args.dsn) if args.dsn else default_db()
    if args.dsn:
        db.connect(init_schema=True)

    tree = json.loads(
        (Path(__file__).resolve().parent.parent / "content" / "curriculum" / "grade6-up.json")
        .read_text(encoding="utf-8")
    )
    generators = {
        (subject["code"], unit["ordinal"], topic["ordinal"]): topic.get("generator")
        for subject in tree["subjects"]
        for unit in subject["units"]
        for topic in unit["topics"]
    }

    rows = db.query(
        "SELECT t.id AS topic_id, t.ordinal AS topic_ordinal, u.id AS unit_id, u.ordinal AS unit_ordinal,"
        " b.id AS textbook_id, s.code AS subject_code"
        " FROM topic t JOIN unit u ON u.id = t.unit_id"
        " JOIN textbook b ON b.id = u.textbook_id JOIN subject s ON s.id = b.subject_id"
    )
    if not rows:
        print("no topics in the database; run import_curriculum first", file=sys.stderr)
        return 1

    counts = {"created": 0, "skipped": 0, "rejected": 0, "no-template": 0}
    for row in rows:
        key = (row["subject_code"], int(row["unit_ordinal"]), int(row["topic_ordinal"]))
        generator_name = generators.get(key)
        if not generator_name:
            counts["no-template"] += 1
            continue
        template = TEMPLATES.get(generator_name)
        if template is None:
            print(f"  unknown template {generator_name!r} for {key}", file=sys.stderr)
            continue
        for slot in range(args.per_topic):
            # Seeded per (topic, slot): the bank is reproducible, and a re-run cannot silently change a
            # question a child has already answered.
            rng = random.Random(f"{row['topic_id']}:{slot}")
            record = template(rng)
            status = upsert_question(
                db,
                topic_id=int(row["topic_id"]),
                textbook_id=int(row["textbook_id"]),
                unit_id=int(row["unit_id"]),
                record=record,
            )
            if status.startswith("rejected"):
                counts["rejected"] += 1
                print(f"  rejected {key} slot {slot}: {status}", file=sys.stderr)
            else:
                counts[status] += 1

    total = db.scalar("SELECT COUNT(*) FROM question")
    by_status = db.query("SELECT verify_status, COUNT(*) AS n FROM question GROUP BY verify_status")
    print(f"seed: {counts['created']} created, {counts['skipped']} already present, "
          f"{counts['rejected']} rejected by sympy, {counts['no-template']} topics without a template")
    print("bank: " + ", ".join(f"{r['verify_status']}={r['n']}" for r in by_status) + f" (total {total})")
    return 0 if counts["rejected"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
