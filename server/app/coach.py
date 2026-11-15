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

#: level -> the line to say. Indexed by `mastery.level_of(score)`; level 0 is never on the weak list.
SAY = (
    "",
    "这类题最近出现得勤一点，做对几次它就少了。",
    "这个知识点错了两三次，我让题多练练它；做对几次它就退回去了。",
    "这块现在最不牢，接下来几天这类题会多一些；连着做对两三次，就少一道。",
)

#: level -> why it is worth practising, for the parent. Not for the child.
WHY = (
    "",
    "刚错了一两次，题量已经多了一点，通常两三天就回去了。",
    "错了三四次，出题权重升到 3 倍，每天最多 3 道。",
    "最不牢的一档，出题权重 4 倍、每天最多 3 道；连错 5 次会停 3 天，不逼他。",
)

#: The line that sounds kind and lands as a judgement. One for all levels.
AVOID = "别说「你怎么又错了」——说的是题，不是他。"


def line(level: int, need_right: int) -> dict:
    """The sentence for one topic. `need_right` is included only when it is 1 or 2: at 7 the number
    reads as a sentence, not an encouragement, and the parent can count the pile on the page instead.
    """
    level = max(0, min(3, int(level)))
    if level == 0:
        return {
            "say": "这个知识点已经回到正常了，不用特意讲。",
            "why": "分数在 11 以下，出题逻辑不再单独照顾它。",
            "avoid": AVOID,
        }
    say = SAY[level]
    need = int(need_right)
    if 1 <= need <= 2:
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
    for level in (1, 2, 3):
        entry = line(level, 7)
        assert entry["say"] and entry["why"] and entry["avoid"], level
        # the exit condition is the point of the copy; it must not be dropped at every level
        assert "次" in entry["say"], entry
        # the child's framing never appears: no score, no level name, no "exam"
        for banned in ("分", "L1", "L2", "L3", "考"):
            assert banned not in entry["say"], (level, banned, entry["say"])
    # a short count is spelled out, a long one is not
    assert "再答对 2 次" in line(2, 2)["say"]
    assert "再答对 7 次" not in line(2, 7)["say"]
    assert line(0, 0)["say"]
    assert "0 个" not in week_line(0) and "1 个" in week_line(1)
    print("coach: ok")


if __name__ == "__main__":
    _selfcheck()
