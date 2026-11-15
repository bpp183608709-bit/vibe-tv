"""Keep the television silent about weak topics (docs/WRONGBOOK.md §10).

The rule is not "show a little less": the television says nothing at all about which topics are weak,
how often they come back, or how many are left. The child sees questions, and the only thing he can
notice is that one kind of question shows up more often and then goes away. Everything else lives on
the parent's phone.

That rule is easy to state and easy to break by accident — a well-meaning "今天有 2 道是复习题" on the
result page would undo it in one line. So this file checks it mechanically, three ways:

1. **Words.** No banned word may appear anywhere under `app/` — not in Kotlin, not in any of the five
   `strings.xml`, not in a comment that someone will later turn into a string.
2. **Fields.** The TV may only read JSON keys it already read before this feature. The set below is
   the whole wire vocabulary; a new key has to be added here on purpose, which is the review moment.
3. **Identifiers.** Names that only exist because of the mastery model (`need_right`, `mastery`,
   `review_question_ids`, `mix_state`, ...) must not appear in the Android sources.

Run: `.venv/bin/python -m server.tools.check_tv_silence` (also called at the end of acceptance.py)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
APP = ROOT / "app" / "src" / "main"

#: Words that turn "a question showed up again" into "you are being made to study". A single one of
#: these on screen is enough to build the feeling this feature exists to avoid.
BANNED_WORDS = (
    "薄弱", "复习", "巩固", "加强", "强化", "专项", "达标", "过关", "错题训练",
)

#: Identifiers that would only be there to carry mastery state to the TV.
BANNED_IDENTIFIERS = (
    "need_right", "review_question_ids", "mix_state", "is_review", "mastery",
    "weight_x10", "paused_until", "topic_mastery",
)

#: Every JSON key the television reads, anywhere in `quiz/`. Add a line here and it becomes admissible;
#: that edit is the point at which someone has to argue that the child may see it.
WIRE_KEYS = frozenset({
    # a served question
    "id", "topic_id", "qtype", "stem", "angle", "difficulty", "options", "accepted",
    "label", "text", "correct",
    # the group and today's allowance
    "session_id", "day_key", "questions", "quota",
    "success_count", "max_success", "awarded_ms", "max_award_ms",
    "attempt_count", "max_attempts", "attempts_left", "award_available",
    # the verdict
    "score", "total", "pass_line", "passed", "award_ms", "ticket", "review",
    "cooldown_seconds", "flags", "capped", "replayed",
    # one wrong answer, for the group's own review page
    "question_id", "subject", "topic", "chosen", "explanation",
    # settings and pairing, which predate this feature
    "config", "pair_code", "name", "error", "detail",
    # ticket claims
    "v", "alg", "jti", "did", "day", "ms", "iat", "exp",
})

_SKIP_SUFFIX = (".png", ".webp", ".jpg", ".jpeg", ".ttf", ".otf", ".jar", ".zip")


def _text_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() in _SKIP_SUFFIX:
            continue
        try:
            yield path, path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue


def scan() -> list[str]:
    problems: list[str] = []

    for path, text in _text_files(APP):
        try:
            rel = path.relative_to(ROOT)
        except ValueError:          # only when a test points APP somewhere else
            rel = path
        for word in BANNED_WORDS:
            if word in text:
                line = next((i for i, l in enumerate(text.splitlines(), 1) if word in l), 0)
                problems.append(f"{rel}:{line}: banned word {word!r}")
        for name in BANNED_IDENTIFIERS:
            if name in text:
                problems.append(f"{rel}: banned identifier {name!r}")

    quiz_dir = APP / "java" / "app" / "orchard" / "tv" / "quiz"
    for path in sorted(quiz_dir.glob("*.kt")):
        text = path.read_text(encoding="utf-8")
        for key in sorted(set(re.findall(r'opt[A-Za-z]*\("([a-z_]+)"\)', text))):
            if key not in WIRE_KEYS:
                problems.append(f"{path.name}: reads new key {key!r} — the child may not see that")

    return problems


def main() -> int:
    problems = scan()
    if problems:
        print("the television is not silent:", file=sys.stderr)
        for problem in problems:
            print("  " + problem, file=sys.stderr)
        return 1
    print(f"tv silence: ok ({len(WIRE_KEYS)} wire keys, {len(BANNED_WORDS)} banned words)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
