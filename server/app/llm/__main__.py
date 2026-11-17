"""Command line for the model side: prove the key works, or generate a batch of questions.

    python -m server.app.llm ping
    python -m server.app.llm gen --subject math --unit "第 1 单元" --topic "分数乘整数" --count 2

`ping` is the deployment smoke test: it proves that the vault injected a key, that DNS and TLS work
from this box, and that the model name still exists — in one call that costs a fraction of a fen. It
prints the outcome, never the answer, never the key.

`gen` prints the questions themselves to stdout. That output is content, not a log: it is meant to be
read by a person or piped into a file by whoever is building the bank (docs/BANK.md). It must never be
the thing systemd captures, which is why `gen` is a tool and not a route.

Maths is re-derived here with sympy before anything is printed, so a batch that does not add up is
obvious immediately rather than three steps later.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent.parent))

from server.app.llm import client, prompts  # noqa: E402

#: Anything outside this is not arithmetic and never reaches sympy. `sympify` walks into eval-like
#: territory, so the expression handed to it is checked against this set first — same rule as
#: `server/tools/gen_seed_questions.py`, which is where the bank's own generator learned it.
EXPR_SAFE = set("0123456789+-*/(). ")


def _math_ok(expr: str, value: str) -> tuple[bool, str]:
    """Recompute `expr` and compare it with `value`. Returns (ok, why-not)."""
    from fractions import Fraction

    if not expr or not value:
        return False, "no check_expr/check_value"
    if not set(expr) <= EXPR_SAFE:
        return False, f"unsafe characters in {expr!r}"
    try:
        import sympy as sp
    except ImportError:  # pragma: no cover - sympy is a deployment dependency for this path
        return False, "sympy not installed"

    try:
        computed = sp.nsimplify(sp.sympify(expr, evaluate=True))
        declared = sp.nsimplify(sp.sympify(value.replace("%", "/100"), evaluate=True))
    except Exception:  # noqa: BLE001 - a model can produce anything; this is a filter, not a parser
        return False, "could not evaluate"
    if sp.simplify(computed - declared) != 0:
        return False, f"{expr} = {computed}, but the answer says {value}"
    return True, ""


def cmd_ping(_: argparse.Namespace) -> int:
    started = time.monotonic()
    reply = client.chat(prompts.PING, temperature=0, max_tokens=8, interactive=True)
    text = client.content_of(reply)
    usage = reply.get("usage") or {}
    print(
        f"ok model={client.model()} ms={int((time.monotonic() - started) * 1000)} "
        f"chars={len(text)} prompt_tokens={usage.get('prompt_tokens', -1)} "
        f"completion_tokens={usage.get('completion_tokens', -1)}"
    )
    return 0


def cmd_gen(args: argparse.Namespace) -> int:
    messages = prompts.question_messages(
        subject=args.subject,
        unit=args.unit,
        topic=args.topic,
        count=args.count,
        difficulty=args.difficulty,
        angle=args.angle,
        qtype=args.qtype,
    )
    reply = client.chat(messages, temperature=args.temperature, max_tokens=4096, json_mode=True)
    raw = client.content_of(reply)
    try:
        payload = json.loads(raw)
    except ValueError:
        print("the model did not return json; nothing was written", file=sys.stderr)
        return 3

    items = payload.get("items") or []
    if not items:
        print("json came back without items", file=sys.stderr)
        return 3

    failures = 0
    for index, item in enumerate(items, start=1):
        stem = str(item.get("stem") or "")
        if len(stem) > 120:
            print(f"item {index}: stem is {len(stem)} chars, over the 120 limit", file=sys.stderr)
            failures += 1
        if item.get("qtype") == "choice":
            options = item.get("options") or []
            labels = [str(option.get("label")) for option in options]
            if len(options) != 4 or len(set(labels)) != 4:
                print(f"item {index}: a choice needs four distinct options", file=sys.stderr)
                failures += 1
            if str(item.get("correct_label") or "") not in labels:
                print(f"item {index}: correct_label is not one of the options", file=sys.stderr)
                failures += 1
        if args.subject == "math":
            ok, why = _math_ok(str(item.get("check_expr") or ""), str(item.get("check_value") or ""))
            if not ok:
                print(f"item {index}: maths does not verify -- {why}", file=sys.stderr)
                failures += 1
        if args.subject in ("chinese", "english") and not item.get("source_ref"):
            print(f"item {index}: no source_ref; a fact without a source is not usable", file=sys.stderr)
            failures += 1

    print(json.dumps({"topic": args.topic, "model": client.model(), "items": items},
                     ensure_ascii=False, indent=2))
    if failures:
        print(f"{failures} problem(s) above; these items must not be imported", file=sys.stderr)
        return 4
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DeepSeek side: ping, or draft questions for one topic.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ping", help="one cheap call: proves the key, the network and the model name")

    gen = sub.add_parser("gen", help="draft questions for one topic and verify the maths")
    gen.add_argument("--subject", required=True, choices=["math", "chinese", "english"])
    gen.add_argument("--unit", required=True)
    gen.add_argument("--topic", required=True)
    gen.add_argument("--count", type=int, default=2)
    gen.add_argument("--difficulty", type=int, default=2, choices=[1, 2, 3])
    gen.add_argument("--angle", default="understand", choices=["recall", "understand", "apply"])
    gen.add_argument("--qtype", default="choice", choices=["choice", "fill"])
    gen.add_argument("--temperature", type=float, default=0.7)

    args = parser.parse_args(argv)
    client.event("INFO", "llm_cli", command=args.command, model=client.model(),
                 key_present=client.key_present())
    try:
        if args.command == "ping":
            return cmd_ping(args)
        return cmd_gen(args)
    except client.LLMError as exc:
        client.event("ERROR", "llm_cli_failed", kind=exc.kind, status=exc.status or "-")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
