"""Minimal, dependency-free structured-output validation (plan.md section 15 / F-03).

NAO must not trust an agent's claim that it produced a verdict. The declared
output schema is enforced: missing required fields, wrong types, unexpected
shapes and out-of-enum values are all violations. A missing/invalid payload is a
verification failure, never a silent success.
"""

from __future__ import annotations

from typing import Any

_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "boolean": (bool,),
    "integer": (int,),
    "number": (int, float),
}


def _type_ok(value: Any, expected: str) -> bool:
    candidates = _TYPE_MAP.get(expected)
    if candidates is None:
        return True
    if expected == "integer" and isinstance(value, bool):
        # bool is a subclass of int in Python; reject it explicitly.
        return False
    if expected == "number" and isinstance(value, bool):
        return False
    return isinstance(value, candidates)


def validate(instance: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Return a list of human-readable schema violations (empty == valid).

    Supports the subset NAO declares: ``type``, ``required``, ``properties``,
    ``additionalProperties``, ``items``, ``minItems`` and ``enum``. Unknown
    keywords are ignored. ``additionalProperties: false`` is enforced so an
    undeclared property is a failure, not silently ignored (F-03).
    """
    errors: list[str] = []
    if not isinstance(schema, dict):
        return errors

    expected_type = schema.get("type")
    if expected_type is not None and not _type_ok(instance, expected_type):
        errors.append(
            f"{path}: expected {expected_type}, got {type(instance).__name__}"
        )
        return errors

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value {instance!r} is not one of {schema['enum']}")

    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in instance:
                errors.append(f"{path}: missing required property '{required}'")
        for key, subschema in properties.items():
            if key in instance:
                errors.extend(validate(instance[key], subschema, f"{path}.{key}"))
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in properties:
                    errors.append(
                        f"{path}: unexpected property '{key}'"
                    )

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(
                f"{path}: expected at least {schema['minItems']} item(s), "
                f"got {len(instance)}"
            )
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                errors.extend(
                    validate(item, item_schema, f"{path}[{index}]")
                )

    return errors


def is_valid(instance: Any, schema: dict[str, Any]) -> bool:
    return not validate(instance, schema)
