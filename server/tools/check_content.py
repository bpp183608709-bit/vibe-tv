"""Retired location. The checker now lives with the data it checks.

    python3 -m server.tools.check_content              # still works, delegates
    python knowledge-eval/scripts/check_content.py     # the real one

The old implementation validated a *translated* copy of the nested tree: it built a flat node object
from `unit` / `topic` rows and then validated that object, so the schema was checking the translator
rather than the data. Both the tree and the translator are gone; the files under knowledge-eval/data/
are already in the validated shape and this shim exists only so an old command line does not break.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parents[2] / "knowledge-eval" / "scripts" / "check_content.py"

if __name__ == "__main__":
    sys.argv[0] = str(TARGET)
    runpy.run_path(str(TARGET), run_name="__main__")
