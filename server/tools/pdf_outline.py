"""Turn the official textbook PDF's table of contents into text the tree can be built from.

The source is the national platform (basic.smartedu.cn), which serves the textbooks as PDF after a
login. The download is done by hand — the site's session cookie is not something to script around, and
one 教材 is three clicks. This tool starts where the download ends: a PDF on disk.

    python3 server/tools/pdf_outline.py ~/Downloads/math6a.pdf
    python3 server/tools/pdf_outline.py ~/Downloads/math6a.pdf --pages 1-6

Three extraction paths, picked by what is installed:

  1. `pdftotext` (poppler) — best when the PDF has a real text layer. One line, exact characters.
  2. `tesseract` — when the page is a scan. Needs `pdftoppm` first to make PNGs. Chinese: `-l chi_sim`.
  3. neither — prints the page range to export and how to install the two tools.

Neither tool is installed in this repository's development sandbox, so **this script has not been run
end to end here**; the branches are written but only the "neither" path has executed. Install poppler
and tesseract on the machine that has the PDFs (macOS: `brew install poppler tesseract tesseract-lang`)
and run it there before trusting any output.

Output goes to stdout as plain text, deliberately: the next step is a human reading it next to
`knowledge-eval/data/nodes_<subject>_6up.json` and fixing the differences. Automating that comparison is
not worth it for 30 rows, and an automatic merge of two unverified lists is how a wrong topic name
survives into production.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def _pages(spec: str) -> str:
    """"1-6" -> "1-6"; kept as a string because both tools take the same range syntax."""
    return spec.strip()


def _run(cmd: list[str]) -> tuple[bool, str]:
    result = subprocess.run(cmd, capture_output=True)
    ok = result.returncode == 0
    text = result.stdout.decode("utf-8", "replace") if ok else result.stderr.decode("utf-8", "replace")
    return ok, text


def with_pdftotext(pdf: Path, pages: str) -> str | None:
    ok, out = _run([
        "pdftotext", "-layout",
        "-f", pages.split("-")[0],
        "-l", pages.split("-")[-1],
        str(pdf), "-",
    ])
    return out if ok and out.strip() else None


def with_tesseract(pdf: Path, pages: str, lang: str) -> str | None:
    if shutil.which("pdftoppm") is None:
        return None
    work = pdf.parent / f"{pdf.stem}-ocr"
    work.mkdir(exist_ok=True)
    first, last = pages.split("-")[0], pages.split("-")[-1]
    ok, err = _run([
        "pdftoppm", "-r", "300", "-png",
        "-f", first, "-l", last,
        str(pdf), str(work / "page"),
    ])
    if not ok:
        print(err, file=sys.stderr)
        return None
    chunks = []
    for image in sorted(work.glob("page*.png")):
        ok, text = _run(["tesseract", str(image), "stdout", "-l", lang, "--psm", "6"])
        chunks.append(f"--- {image.name} ---\n{text if ok else ''}")
    return "\n".join(chunks) if chunks else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract a textbook PDF's contents pages as text.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--pages", default="1-6", help="page range (default 1-6: cover..contents)")
    parser.add_argument("--lang", default="chi_sim", help="tesseract language pack (default chi_sim)")
    args = parser.parse_args(argv)

    if not args.pdf.exists():
        print(f"no such file: {args.pdf}", file=sys.stderr)
        return 1
    pages = _pages(args.pages)

    if shutil.which("pdftotext"):
        text = with_pdftotext(args.pdf, pages)
        if text:
            print(text)
            return 0
        print("pdftotext produced nothing (scanned pages?); trying tesseract", file=sys.stderr)

    if shutil.which("tesseract"):
        text = with_tesseract(args.pdf, pages, args.lang)
        if text:
            print(text)
            return 0
        print("tesseract produced nothing", file=sys.stderr)

    print(
        "needs poppler and/or tesseract.\n"
        "  macOS:  brew install poppler tesseract tesseract-lang\n"
        "  Debian: apt install poppler-utils tesseract-ocr tesseract-ocr-chi-sim\n"
        f"Then:    pdftotext -layout -f 1 -l 6 '{args.pdf}' -   # try this first\n"
        f"         pdftoppm -r 300 -png -f 1 -l 6 '{args.pdf}' page && "
        "tesseract page-1.png stdout -l chi_sim\n"
        "\n"
        "Paste the output back and the tree gets corrected by hand; see docs/QUIZ.md §5.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
