from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class DryRunError(ValueError):
    pass


def dry_run(kit_dir: Path, tool_name: str, args: dict[str, Any], confirmed: bool = False) -> dict[str, Any]:
    capabilities = load_capabilities(kit_dir)
    guardrails = json.loads((kit_dir / "guardrails" / "permissions.json").read_text(encoding="utf-8"))
    capability = capabilities.get(tool_name)
    if not capability:
        raise DryRunError(f"Unknown tool: {tool_name}")
    rule = guardrails.get("tools", {}).get(tool_name)
    if not rule:
        raise DryRunError(f"Missing guardrail for tool: {tool_name}")
    validation = validate_args(capability.get("input_schema", {}), args)
    allowed = not validation["errors"] and (not rule["confirm_required"] or confirmed)
    return {
        "tool": tool_name,
        "allowed": allowed,
        "would_execute": False,
        "confirmed": confirmed,
        "requires_confirmation": rule["confirm_required"],
        "risk": rule["risk"],
        "risk_reason": rule.get("reason", ""),
        "validation": validation,
        "transport": rule.get("transport", {}),
        "planned_call": {
            "type": rule.get("transport", {}).get("type", "unknown"),
            "args": args,
        },
        "next_step": next_step(validation["errors"], rule["confirm_required"], confirmed),
    }


def load_capabilities(kit_dir: Path) -> dict[str, dict[str, Any]]:
    data = json.loads((kit_dir / "capabilities.json").read_text(encoding="utf-8"))
    return {item["name"]: item for item in data}


def validate_args(schema: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    required = set(schema.get("required", []))
    properties = schema.get("properties", {})
    errors: list[str] = []
    for key in sorted(required):
        if key not in args:
            errors.append(f"Missing required argument: {key}")
    if schema.get("additionalProperties") is False:
        for key in sorted(args):
            if key not in properties:
                errors.append(f"Unexpected argument: {key}")
    for key, value in args.items():
        expected = properties.get(key, {}).get("type") if isinstance(properties.get(key), dict) else None
        if expected and not matches_type(expected, value):
            errors.append(f"Argument {key} expected {expected}, got {type(value).__name__}")
    return {"valid": not errors, "errors": errors}


def matches_type(expected: str, value: Any) -> bool:
    if expected in {"string"}:
        return isinstance(value, str)
    if expected in {"number", "integer"}:
        return isinstance(value, int | float)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def next_step(errors: list[str], confirm_required: bool, confirmed: bool) -> str:
    if errors:
        return "Fix invalid tool arguments before execution."
    if confirm_required and not confirmed:
        return "Ask a human to explicitly confirm this high-risk operation."
    return "Safe to execute through the host system adapter."
