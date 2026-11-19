"""The mastery report, grouped the way the textbook is (docs/RULES.md §2.6).

A chapter is a row; the diagnostic nodes inside it are the lines. The chapter itself is never judged —
it has no questions of its own, so "第三章薄弱" would be a statement about nothing. What it does carry
is the two counts a parent actually asks for: how many nodes this chapter has, and how many of them are
weak (docs/WRONGBOOK.md §6.8: 报点数，不报全库总数).

Pure functions only: `build` takes rows that `h5.report_by_node` has already resolved (names, levels,
accuracies, ability breakdown, evidence ids) and returns the JSON in `knowledge-eval/schemas/report.schema.json`. The numbers are not recomputed here — one place decides a level, and it is
mastery.py.

The sentence at the end of each line is advice, not a plan: the product does not push a practice pack
(§5.4 of the plan), it tells the parent what to say and what to practise next.
"""

from __future__ import annotations

#: level -> the label the report prints. "待测" is not a level; it is what a node with fewer than three
#: answers reads as, and it keeps a report from accusing a child of not knowing something untested.
LABELS = ("0-未掌握", "1-基础", "2-熟练", "3-拓展")

#: level -> advice. Written for the parent, about the next ten minutes, not about the term.
SUGGESTIONS = (
    "先别急着讲新内容：把这个节点的例题重做一遍，再让他自己说一遍是怎么想的。"
    "连着答对两三道再往前走。",
    "标准题基本能做了，变式还不稳。先练「换个说法还认不认得」，再回到应用题。",
    "已经比较稳了。隔几天用一两道题保持手感就行，不用专门讲。",
    "掌握得好。可以让他给别的知识点讲一讲，讲得清楚才算真的会。",
)

#: ability_tag -> what to call it in a note. Missing tags fall back to the tag itself.
ABILITY_WORDS = {
    "理解意义": "理解意义", "执行计算": "执行计算", "情境应用": "情境应用", "辨析变式": "辨析变式",
    "语言运用": "语言运用", "阅读理解": "阅读理解", "辨析判断": "辨析判断", "综合表达": "综合表达",
}


def build(groups: list[dict], period: str, scope: str = "weak", source: str = "db") -> dict:
    """`groups` is one entry per chapter, already carrying its diagnostic rows.

    Each group: {aggregate_id, aggregate_name, diagnostics: [{node_id, node_name, level, acc,
    sample, evidence, abilities}]}.

    `scope='weak'` (the default, and the only thing the report is for) puts **weak** nodes in
    `diagnostics` and nothing else: measured (sample >= 3) and level <= 1. `scope='all'` adds
    `full_view` with every measured node, 熟练 and 拓展 included; the weak list stays weak either way,
    so a caller cannot accidentally turn "what to teach this week" into "the whole term".
    """
    summary = []
    for group in groups:
        diagnostics = []
        full_view = []
        weak = 0
        for row in group["diagnostics"]:
            level = int(row["level"])
            sample = int(row["sample"])
            if not row.get("measured", sample >= 3):
                continue
            line = _line(row, level, sample)
            if level <= 1:
                weak += 1
                diagnostics.append(line)
            if scope == "all":
                full_view.append(line)
        chapter = {
            "aggregate_node_id": group["aggregate_id"],
            "aggregate_name": group["aggregate_name"],
            "diagnostic_count": group["diagnostic_count"],
            "weak_diagnostic_count": weak,
            "diagnostics": diagnostics,
        }
        if scope == "all":
            chapter["full_view"] = full_view
        summary.append(chapter)
    return {"student_id": groups[0]["student_id"] if groups else "", "period": period,
            "source": source, "scope": scope, "aggregate_summary": summary}


def _line(row: dict, level: int, sample: int) -> dict:
    abilities = row.get("abilities") or []
    return {
        "diagnostic_node_id": row["node_id"],
        "diagnostic_name": row["node_name"],
        "mastery_level": LABELS[level],
        "weighted_accuracy": round(float(row["acc"]) / 1000, 3),
        "sample_count": sample,
        "ability_breakdown": [
            {"tag": a["tag"], "mastery_level": LABELS[int(a.get("level") or 0)],
             "weighted_accuracy": round(int(a["acc"]) / 1000, 3),
             "sample_count": int(a["sample"])}
            for a in abilities
        ],
        "ability_breakdown_note": _note(abilities),
        "evidence_question_ids": [str(q) for q in (row.get("evidence") or [])],
        "suggestion": SUGGESTIONS[level],
    }


def _note(abilities: list[dict]) -> str | None:
    """The weakest ability line, if there is enough evidence to name one.

    The node stays the unit that becomes a 薄弱项 — the weak list and the daily quota count nodes, and
    that does not change. The line is the drill-down under it. Since `ability_mastery` each line is
    judged on its own window, so this sentence names a line that is weak by itself rather than one that
    only looks weak because the node's ten rows happened to be split its way.
    """
    usable = [a for a in abilities if int(a.get("sample") or 0) >= 2]
    if len(usable) < 2:
        return None
    worst = min(usable, key=lambda a: int(a["acc"]))
    if int(worst["acc"]) >= int(max(usable, key=lambda a: int(a["acc"]))["acc"]):
        return None
    name = ABILITY_WORDS.get(worst["tag"], worst["tag"])
    return f"其中「{name}」这条能力线最弱（加权正确率 {int(worst['acc']) / 1000:.2f}），建议优先关注。"


def _selfcheck() -> None:
    """Run: python -m server.app.report"""
    assert len(LABELS) == 4 and len(SUGGESTIONS) == 4
    groups = [{
        "student_id": "dev",
        "aggregate_id": "math-6up-u01",
        "aggregate_name": "分数乘法",
        "diagnostic_count": 3,
        "diagnostics": [
            {"node_id": "math-6up-d01", "node_name": "分数乘整数", "level": 1, "acc": 520, "sample": 8,
             "evidence": [11, 12, 13],
             "abilities": [{"tag": "执行计算", "acc": 700, "sample": 4},
                           {"tag": "辨析变式", "acc": 300, "sample": 4}]},
            {"node_id": "math-6up-d02", "node_name": "分数乘分数", "level": 3, "acc": 900, "sample": 5,
             "evidence": [], "abilities": []},
            {"node_id": "math-6up-d03", "node_name": "运算律", "level": 0, "acc": 0, "sample": 1,
             "evidence": [], "abilities": []},
        ],
    }]
    report = build(groups, "最近 30 天")
    chapter = report["aggregate_summary"][0]
    # the unmeasured node is dropped from the list but still counted in the chapter's total;
    # the 拓展 node is measured but not weak, so the default report does not print it at all
    assert chapter["diagnostic_count"] == 3
    assert len(chapter["diagnostics"]) == 1
    assert chapter["weak_diagnostic_count"] == 1
    assert chapter["diagnostics"][0]["mastery_level"] == "1-基础"
    assert "辨析变式" in chapter["diagnostics"][0]["ability_breakdown_note"]
    assert chapter["diagnostics"][0]["ability_breakdown"][0]["tag"] in ("执行计算", "辨析变式")
    assert "full_view" not in chapter

    # the whole-chapter view is a separate field, and it keeps the weak list as it was
    full = build(groups, "最近 30 天", scope="all")["aggregate_summary"][0]
    assert [line["mastery_level"] for line in full["full_view"]] == ["1-基础", "3-拓展"]
    assert [line["mastery_level"] for line in full["diagnostics"]] == ["1-基础"]
    assert full["weak_diagnostic_count"] == 1
    assert build([], "x")["student_id"] == ""
    print("report: ok")


if __name__ == "__main__":
    _selfcheck()
