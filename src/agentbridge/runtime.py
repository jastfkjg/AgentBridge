from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agentbridge.discovery import dedupe_capabilities
from agentbridge.models import Capability


class DryRunError(ValueError):
    pass


def dry_run(kit_dir: Path, tool_name: str, args: dict[str, Any], confirmed: bool = False) -> dict[str, Any]:
    capabilities, guardrails = load_runtime_kit(kit_dir)
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
    capabilities, _guardrails = load_runtime_kit(kit_dir)
    return capabilities


def load_runtime_kit(kit_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    data = json.loads((kit_dir / "capabilities.json").read_text(encoding="utf-8"))
    raw_items = [item for item in data if isinstance(item, dict) and item.get("name")]
    raw_names = {str(item["name"]) for item in raw_items}
    raw_items = [
        item
        for item in raw_items
        if not has_available_duplicate_replacement(item, raw_names)
    ]
    capabilities: list[Capability] = []
    original_names: dict[int, str] = {}
    for item in raw_items:
        capability = Capability.from_dict(item)
        original_name = capability.name
        legacy_match = re.fullmatch(r"(.+)_([2-9][0-9]*)", original_name)
        if legacy_match and legacy_match.group(1) in raw_names:
            capability.name = legacy_match.group(1)
        capabilities.append(capability)
        original_names[id(capability)] = original_name

    normalized = dedupe_capabilities(capabilities)
    raw_guardrails = json.loads(
        (kit_dir / "guardrails" / "permissions.json").read_text(encoding="utf-8")
    )
    raw_rules = raw_guardrails.get("tools", {})
    normalized_rules: dict[str, Any] = {}
    normalized_capabilities: dict[str, dict[str, Any]] = {}
    for capability in normalized:
        normalized_capabilities[capability.name] = capability.to_dict()
        original_name = original_names[id(capability)]
        rule = raw_rules.get(original_name)
        if not isinstance(rule, dict):
            rule = {
                "risk": capability.risk,
                "confirm_required": capability.confirm_required,
                "transport": capability.transport,
                "resource": capability.resource,
                "action": capability.action,
            }
        normalized_rules[capability.name] = rule

    guardrails = dict(raw_guardrails)
    guardrails["tools"] = normalized_rules
    return normalized_capabilities, guardrails


def has_available_duplicate_replacement(item: dict[str, Any], raw_names: set[str]) -> bool:
    description = str(item.get("description", ""))
    if "scanner-generated duplicate" not in description.lower():
        return False
    match = re.search(r"\buse\s+`?([a-z][a-z0-9_]*)`?\s+instead\b", description, re.IGNORECASE)
    return bool(match and match.group(1) in raw_names)


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
