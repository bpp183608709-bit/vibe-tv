"""Shared helpers for the knowledge-eval scripts.

Everything here is file I/O. Nothing imports the server, opens a database, or reaches the network, so
`python knowledge-eval/scripts/evaluate.py ...` works in a clean checkout with no MySQL and no venv
beyond the standard library.

Layout (paths are resolved from this file, so the scripts can be run from anywhere):

    knowledge-eval/
      data/      nodes_<subject>_6up.json, questions_<subject>_6up.json, taxonomy/
      schemas/   node / question / report
      scripts/   this module and the checkers
      tests/     fixtures + negative tests

The schemas are applied to the objects exactly as they appear in the files. There is no renaming or
reshaping step between "read the JSON" and "validate the object" — that indirection is what let the
previous revision pass its schema while the data did not match the design.
"""

from __future__ import annotations

import json
from pathlib import Path

KE = Path(__file__).resolve().parent.parent
REPO = KE.parent
DATA = KE / "data"
SCHEMAS = KE / "schemas"
TAXONOMY = DATA / "taxonomy" / "pep-math-6up-v3.json"

SUBJECTS = ("math", "chinese", "english")

#: What the repo's JSON-Schema subset understands. Kept here so a schema edit that uses a new keyword
#: fails loudly instead of being silently ignored.
SUPPORTED_KEYWORDS = {"$schema", "$id", "title", "description", "type", "required", "properties",
                      "additionalProperties", "items", "enum", "pattern", "minLength", "minItems",
                      "minimum", "maximum", "definitions"}

_TYPES = {
    "object": dict, "array": list, "string": str, "integer": int,
    "number": (int, float), "boolean": bool, "null": type(None),
}


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def schema(name: str) -> dict:
    return read_json(SCHEMAS / name)


def validate(value, spec: dict, path: str = "$") -> list[str]:
    """A small draft-07 subset: type, required, properties, additionalProperties, items, enum,
    pattern, minLength, minItems, minimum, maximum. Enough for the three schemas here, and small
    enough that the check itself is readable."""
    errors: list[str] = []
    kinds = spec.get("type")
    if kinds is not None:
        allowed = kinds if isinstance(kinds, list) else [kinds]
        if not any(_is_type(value, kind) for kind in allowed):
            return [f"{path}: expected {' or '.join(allowed)}, got {type(value).__name__}"]

    if "enum" in spec and value not in spec["enum"]:
        errors.append(f"{path}: {value!r} is not one of {spec['enum']}")

    if isinstance(value, str):
        if spec.get("pattern") and not _matches(spec["pattern"], value):
            errors.append(f"{path}: {value!r} does not match {spec['pattern']}")
        if spec.get("minLength") is not None and len(value) < spec["minLength"]:
            errors.append(f"{path}: shorter than {spec['minLength']} characters")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in spec and value < spec["minimum"]:
            errors.append(f"{path}: below {spec['minimum']}")
        if "maximum" in spec and value > spec["maximum"]:
            errors.append(f"{path}: above {spec['maximum']}")
    elif isinstance(value, list):
        if "minItems" in spec and len(value) < spec["minItems"]:
            errors.append(f"{path}: needs at least {spec['minItems']} items, has {len(value)}")
        if isinstance(spec.get("items"), dict):
            for index, item in enumerate(value):
                errors.extend(validate(item, spec["items"], f"{path}[{index}]"))
    elif isinstance(value, dict):
        for key in spec.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required field {key!r}")
        properties = spec.get("properties", {})
        for key, item in value.items():
            if key in properties:
                errors.extend(validate(item, properties[key], f"{path}.{key}"))
            elif spec.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected field {key!r}")
    return errors


def _matches(pattern: str, value: str) -> bool:
    import re
    return re.search(pattern, value) is not None


def _is_type(value, kind: str) -> bool:
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    expected = _TYPES[kind]
    if expected is bool:
        return isinstance(value, bool)
    return isinstance(value, expected)


def node_files(paths: list[str] | None = None) -> list[Path]:
    if paths:
        return [Path(p) for p in paths]
    return [DATA / f"nodes_{subject}_6up.json" for subject in SUBJECTS]


def question_files(paths: list[str] | None = None) -> list[Path]:
    if paths:
        return [Path(p) for p in paths]
    return [DATA / f"questions_{subject}_6up.json" for subject in SUBJECTS]


def load_nodes(paths: list[str] | None = None) -> list[tuple[Path, dict]]:
    """[(file, payload)] — the payload keeps its header, because `subject` and `taxonomy_version` live
    there and the node objects themselves have to stay exactly as written."""
    return [(path, read_json(path)) for path in node_files(paths)]


def nodes_of(payload: dict) -> list[dict]:
    return payload["nodes"]


def all_nodes(paths: list[str] | None = None) -> list[dict]:
    return [node for _, payload in load_nodes(paths) for node in payload["nodes"]]


def load_questions(paths: list[str] | None = None) -> list[tuple[Path, dict]]:
    return [(path, read_json(path)) for path in question_files(paths)]


def all_questions(paths: list[str] | None = None) -> list[dict]:
    return [q for _, payload in load_questions(paths) for q in payload["questions"]]


def load_taxonomy(path: str | Path | None = None) -> dict:
    return read_json(path or TAXONOMY)
