"""A very small JSON-Schema subset, so the content schemas can be enforced without a dependency.

The three files in `server/content/schema/` are the spec (a human and any external tool can read them);
this module is only what `check_content.py` and `evaluate_report.py` need to hold the files to it:
type, required, properties, additionalProperties, items, enum, pattern, minLength, minItems, minimum,
maximum.

Anything a schema uses outside that list is ignored rather than guessed at, and `_selftest` at the bottom
fails loudly if one of our own schema files starts relying on an unsupported keyword.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "content" / "schema"

SUPPORTED = {"$schema", "$id", "title", "description", "type", "required", "properties",
             "additionalProperties", "items", "enum", "pattern", "minLength", "minItems",
             "minimum", "maximum"}

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def load(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def check(value: Any, schema: dict, path: str = "$") -> list[str]:
    """Every violation of `schema` by `value`, as 'path: what is wrong' strings."""
    errors: list[str] = []

    kinds = schema.get("type")
    if kinds is not None:
        allowed = kinds if isinstance(kinds, list) else [kinds]
        # bool is an int in Python; a JSON `true` must not satisfy "integer".
        if not any(_is_type(value, kind) for kind in allowed):
            return [f"{path}: expected {' or '.join(allowed)}, got {type(value).__name__}"]

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")

    if isinstance(value, str):
        pattern = schema.get("pattern")
        if pattern and not re.search(pattern, value):
            errors.append(f"{path}: {value!r} does not match {pattern}")
        min_length = schema.get("minLength")
        if min_length is not None and len(value) < min_length:
            errors.append(f"{path}: shorter than {min_length} characters")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above {schema['maximum']}")
    elif isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: needs at least {schema['minItems']} items, has {len(value)}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(check(item, item_schema, f"{path}[{index}]"))
    elif isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required field {key!r}")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                errors.extend(check(item, properties[key], f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected field {key!r}")

    return errors


def _is_type(value: Any, kind: str) -> bool:
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    expected = _TYPES[kind]
    if expected is bool:
        return isinstance(value, bool)
    return isinstance(value, expected)


def _selftest() -> None:
    for name in ("node.schema.json", "question.schema.json", "report.schema.json"):
        schema = load(name)
        stack = [schema]
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            unknown = set(node) - SUPPORTED
            if unknown:
                raise SystemExit(f"{name}: unsupported keyword(s) {sorted(unknown)}")
            stack.extend(node.get("properties", {}).values())
            if isinstance(node.get("items"), dict):
                stack.append(node["items"])

    assert check("x", {"type": "integer"}) != []
    assert check(True, {"type": "integer"}) != []
    assert check({"a": 1}, {"type": "object", "required": ["b"]}) != []
    assert check([1], {"type": "array", "minItems": 2}) != []
    assert check("ab", {"type": "string", "minLength": 3}) != []
    assert check(3, {"type": "integer", "minimum": 4}) != []


_selftest()
