"""What the parent says out loud, since the television says nothing (docs/WRONGBOOK.md §11).

The child will notice that one kind of question keeps coming back, and will ask why. §10 decided the
TV never answers that — no label, no counter, no "review" wording. The answer therefore has to come
from the person in the room, and that person is not the one holding the score. This module is the one
sentence they can read off the page and say.

Two rules for the copy, and they pull against each other:

* **It has to be true.** "The questions are random" is the tempting line and it is a lie; the parent
  would be defending a mechanism they are looking at on the same screen.
* **It has to be about the questions, not the child.** "You keep getting these wrong" turns a queue
  into a verdict. The child can act on "these come back until you get a few right"; there is nothing
  to act on in "you are bad at fractions".

So each line names the *questions* as the subject and gives the exit condition in the same breath —
the exit condition is the honest part, and it is also the part that makes the frequency feel finite
rather than punitive.

`why` is for the parent only: it explains the mechanism so they are not reciting a script they do not
understand. `avoid` is the sentence that sounds supportive and is not.

Nothing here is imported by the TV path. Run `python -m server.app.coach` for the checks.
"""

from __future__ import annotations

#: level -> the line to say. Indexed by the mastery level: 0 未掌握, 1 基础. Levels 2 and 3 are not on
#: the weak list at all, so their lines exist only for a detail page opened from an old link.
SAY = (
    "这块眼下最不牢，接下来几天这类题会多一些；连着做对几次，就少一道。",
    "这个知识点对了一半左右，我让题多练练它；做对几次它就退回去了。",
    "这个知识点已经回到正常了，不用特意讲。",
    "这个知识点已经回到正常了，不用特意讲。",
)

#: level -> why it is worth practising, for the parent. Not for the child.
WHY = (
    "最近 10 道题加权正确率不到 40%，出题权重 4 倍、每天最多 3 道；连错 5 次会停 3 天，不逼他。",
    "正确率在 40%–60%，出题权重 2 倍，每天最多 3 道。",
    "正确率已在 60% 以上，出题逻辑不再单独照顾它。",
    "正确率已在 80% 以上且答对过难题，出题逻辑不再单独照顾它。",
)

#: The line that sounds kind and lands as a judgement. One for all levels.
AVOID = "别说「你怎么又错了」——说的是题，不是他。"


def line(level: int, need_right: int, need_sample: int = 0) -> dict:
    """The sentence for one topic.

    `need_sample` comes first: below three answers nothing has been judged, and promising "再答对 2 次"
    for a node nobody has measured would be inventing a measurement. `need_right` is included only when
    it is 1 or 2 — at 7 the number reads as a sentence, not an encouragement, and the parent can count
    the pile on the page instead.
    """
    level = max(0, min(3, int(level)))
    if int(need_sample) > 0:
        return {
            "say": f"这块还没测出来，再答 {int(need_sample)} 道题就能看出掌握得怎么样。",
            "why": "少于 3 道题不作判断：一题对错说明不了问题，判早了反而冤枉他。",
            "avoid": AVOID,
        }
    say = SAY[level]
    need = int(need_right)
    if 1 <= need <= 2 and level <= 1:
        say = f"{say}再答对 {need} 次就够了。"
    return {"say": say, "why": WHY[level], "avoid": AVOID}


def week_line(n: int) -> str:
    """The one sentence at the top of the list page: how many, and how many to aim for this week."""
    n = int(n)
    if n <= 0:
        return "暂时没有要讲的知识点。错题会自动折算到这里。"
    return f"现在有 {n} 个知识点在练。一周讲掉 1–2 个就够，不用全清。"


def _selfcheck() -> None:
    """Run: python -m server.app.coach"""
    for level in (0, 1):
        entry = line(level, 7)
        assert entry["say"] and entry["why"] and entry["avoid"], level
        # the exit condition is the point of the copy; it must not be dropped at every weak level
        assert "次" in entry["say"], entry
        # the child's framing never appears: no score, no level name, no "exam"
        for banned in ("L0", "L1", "L2", "L3", "考"):
            assert banned not in entry["say"], (level, banned, entry["say"])
    # a node with too few answers asks for samples, not for right answers
    assert "再答 2 道题" in line(0, 0, need_sample=2)["say"]
    # a short count is spelled out, a long one is not
    assert "再答对 2 次" in line(1, 2)["say"]
    assert "再答对 7 次" not in line(1, 7)["say"]
    # levels that are not weak never promise anything
    assert line(2, 0)["say"] and line(3, 0)["say"]
    assert "0 个" not in week_line(0) and "1 个" in week_line(1)
    print("coach: ok")


if __name__ == "__main__":
    _selfcheck()
