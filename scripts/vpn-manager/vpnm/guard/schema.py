"""Schema loading, canonical hashing and a tiny structural validator.

The canonical ``guard.*/1`` JSON Schemas live next to this module in
``schemas/`` and are shipped as package data, so ``schema_hash`` and
``vpn guard selftest`` work after ``pip install`` (SPEC AC-12).

``schema_hash`` is the sha256 of the canonical JSON of the schema file:
sorted keys, compact separators, UTF-8 — the same normalisation the daemon
uses for its own payload hashes (:func:`vpnm.guard.events.payload_hash`).

:func:`validate` is **not** a full JSON-Schema implementation.  It covers the
subset the guard contracts actually use (``$ref``, ``type``, ``const``,
``enum``, ``required``, ``properties``, ``items``, ``minimum``/``maximum``,
``pattern``, ``anyOf``) so the tests can catch contract drift without adding a
runtime dependency.  A proper validator may be used in CI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .events import SCHEMA_FILES, payload_hash

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"

_TYPE_CHECKS: Mapping[str, tuple] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "boolean": (bool,),
}


def schema_path(name: str) -> Path:
    """Path of the schema file for *name* (``KeyError`` if unknown)."""
    return SCHEMA_DIR / SCHEMA_FILES[name]


def load_schema(name: str) -> Dict[str, Any]:
    """Parsed schema for *name*."""
    with schema_path(name).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{name}: schema root must be an object")
    return payload


def schema_hash(name: str) -> str:
    """``sha256:<64 hex>`` of the canonical schema JSON for *name*."""
    return payload_hash(load_schema(name))


def schema_hashes() -> Dict[str, str]:
    """All contract hashes, sorted by schema id (stable for ``rules --json``)."""
    return {name: schema_hash(name) for name in sorted(SCHEMA_FILES)}


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    checks = _TYPE_CHECKS.get(expected)
    if checks is None:
        return True
    return isinstance(value, checks)


def _resolve_ref(ref: str, root: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Resolve a local ``#/$defs/x`` or a sibling schema id reference."""
    if ref.startswith("#/"):
        node: Any = root
        for part in ref[2:].split("/"):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node if isinstance(node, dict) else None
    if ref in SCHEMA_FILES:
        return load_schema(ref)
    return None


def validate(payload: Any, name: str, _root: Optional[Dict[str, Any]] = None,
             _path: str = "$") -> List[str]:
    """Return a list of human-readable contract errors (empty when valid)."""
    root = _root if _root is not None else load_schema(name)
    errors: List[str] = []
    _validate_node(payload, root, root, _path, errors)
    return errors


def _validate_node(value: Any, node: Mapping[str, Any],
                   root: Mapping[str, Any], path: str,
                   errors: List[str]) -> None:
    ref = node.get("$ref")
    if isinstance(ref, str):
        target = _resolve_ref(ref, root)
        if target is None:
            errors.append(f"{path}: unresolved $ref {ref}")
            return
        _validate_node(value, target, target, path, errors)
        return

    any_of = node.get("anyOf")
    if isinstance(any_of, list):
        branch_errors = []
        for branch in any_of:
            if not isinstance(branch, dict):
                continue
            trial: List[str] = []
            _validate_node(value, branch, root, path, trial)
            if not trial:
                branch_errors = []
                break
            branch_errors.append(trial)
        if branch_errors:
            errors.append(f"{path}: no anyOf branch matched")
            return

    if "const" in node and value != node["const"]:
        errors.append(f"{path}: expected const {node['const']!r}, got {value!r}")
        return

    enum = node.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{path}: {value!r} not in enum")
        return

    expected = node.get("type")
    if isinstance(expected, str):
        if not _type_ok(value, expected):
            errors.append(f"{path}: expected {expected}, got {type(value).__name__}")
            return
    elif isinstance(expected, list):
        candidates = [item for item in expected if isinstance(item, str)]
        if candidates and not any(_type_ok(value, item) for item in candidates):
            errors.append(
                f"{path}: expected one of {candidates}, got {type(value).__name__}")
            return

    if isinstance(value, str):
        _validate_string(value, node, path, errors)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        _validate_number(value, node, path, errors)

    if isinstance(value, dict):
        _validate_object(value, node, root, path, errors)

    if isinstance(value, list):
        _validate_array(value, node, root, path, errors)


def _validate_string(value: str, node: Mapping[str, Any], path: str,
                     errors: List[str]) -> None:
    pattern = node.get("pattern")
    if isinstance(pattern, str) and not re.search(pattern, value):
        errors.append(f"{path}: does not match {pattern}")
    max_length = node.get("maxLength")
    if isinstance(max_length, int) and len(value) > max_length:
        errors.append(f"{path}: longer than {max_length}")


def _validate_number(value: float, node: Mapping[str, Any], path: str,
                     errors: List[str]) -> None:
    minimum = node.get("minimum")
    if isinstance(minimum, (int, float)) and value < minimum:
        errors.append(f"{path}: {value} < minimum {minimum}")
    maximum = node.get("maximum")
    if isinstance(maximum, (int, float)) and value > maximum:
        errors.append(f"{path}: {value} > maximum {maximum}")


def _validate_object(value: Mapping[str, Any], node: Mapping[str, Any],
                     root: Mapping[str, Any], path: str,
                     errors: List[str]) -> None:
    required = node.get("required")
    if isinstance(required, list):
        for key in required:
            if isinstance(key, str) and key not in value:
                errors.append(f"{path}: missing required {key!r}")

    properties = node.get("properties")
    props = properties if isinstance(properties, dict) else {}
    for key, child in props.items():
        if key in value and isinstance(child, dict):
            _validate_node(value[key], child, root, f"{path}.{key}", errors)

    extra = node.get("additionalProperties")
    if extra is False:
        for key in value:
            if key not in props:
                errors.append(f"{path}: unexpected key {key!r}")
    elif isinstance(extra, dict):
        for key, item in value.items():
            if key not in props:
                _validate_node(item, extra, root, f"{path}.{key}", errors)


def _validate_array(value: Sequence[Any], node: Mapping[str, Any],
                    root: Mapping[str, Any], path: str,
                    errors: List[str]) -> None:
    min_items = node.get("minItems")
    if isinstance(min_items, int) and len(value) < min_items:
        errors.append(f"{path}: fewer than {min_items} items")
    max_items = node.get("maxItems")
    if isinstance(max_items, int) and len(value) > max_items:
        errors.append(f"{path}: more than {max_items} items")
    items = node.get("items")
    if isinstance(items, dict):
        for index, item in enumerate(value):
            _validate_node(item, items, root, f"{path}[{index}]", errors)
