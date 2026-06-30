from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def diff_kits(old_kit: Path, new_kit: Path) -> dict[str, Any]:
    old_caps = _load_capabilities(old_kit)
    new_caps = _load_capabilities(new_kit)
    old_names = set(old_caps)
    new_names = set(new_caps)
    added = sorted(new_names - old_names)
    removed = sorted(old_names - new_names)
    risk_changed: list[dict[str, str]] = []
    changed: list[dict[str, Any]] = []
    for name in sorted(old_names & new_names):
        old = old_caps[name]
        new = new_caps[name]
        if old.get("risk") != new.get("risk"):
            risk_changed.append({"name": name, "from": str(old.get("risk")), "to": str(new.get("risk"))})
        fields = ["description", "input_schema", "transport", "action", "resource", "confirm_required"]
        field_changes = [
            {"field": field, "from": old.get(field), "to": new.get(field)}
            for field in fields
            if old.get(field) != new.get(field)
        ]
        if field_changes or old.get("risk") != new.get("risk"):
            changed.append({"name": name, "fields": field_changes})
    guardrail_changed = _load_json(old_kit / "guardrails" / "permissions.json") != _load_json(new_kit / "guardrails" / "permissions.json")
    result = {
        "old": str(old_kit),
        "new": str(new_kit),
        "added": added,
        "removed": removed,
        "changed": changed,
        "risk_changed": risk_changed,
        "guardrail_changed": guardrail_changed,
    }
    result["has_changes"] = bool(added or removed or changed or risk_changed or guardrail_changed)
    return result


def format_diff(diff: dict[str, Any]) -> str:
    lines = [f"Old kit: {diff.get('old')}", f"New kit: {diff.get('new')}"]
    if not diff.get("has_changes"):
        lines.append("No kit differences.")
        return "\n".join(lines)
    for key in ("added", "removed"):
        values = diff.get(key, [])
        lines.append(f"{key}: {', '.join(values) if values else 'none'}")
    risk_changes = diff.get("risk_changed", [])
    if risk_changes:
        lines.append("risk changed:")
        for item in risk_changes:
            lines.append(f"- {item['name']}: {item['from']} -> {item['to']}")
    changed = diff.get("changed", [])
    if changed:
        lines.append("changed:")
        for item in changed:
            fields = ", ".join(change["field"] for change in item.get("fields", [])) or "risk"
            lines.append(f"- {item['name']}: {fields}")
    if diff.get("guardrail_changed"):
        lines.append("guardrails changed")
    return "\n".join(lines)


def _load_capabilities(kit_dir: Path) -> dict[str, dict[str, Any]]:
    data = _load_json(kit_dir / "capabilities.json")
    if not isinstance(data, list):
        return {}
    return {str(item["name"]): item for item in data if isinstance(item, dict) and item.get("name")}


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
