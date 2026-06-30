from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

SENSITIVE_KEY_MARKERS = {
    "authorization",
    "cookie",
    "password",
    "passwd",
    "pwd",
    "token",
    "secret",
    "api_key",
    "apikey",
    "x-api-key",
}


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in SENSITIVE_KEY_MARKERS or any(marker in lowered for marker in SENSITIVE_KEY_MARKERS):
                redacted[str(key)] = "<redacted>"
            else:
                redacted[str(key)] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def append_audit_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(redact_sensitive(event), sort_keys=True) + "\n")


def read_audit_events(
    path: Path,
    *,
    user: str | None = None,
    session_id: str | None = None,
    tool: str | None = None,
    risk: str | None = None,
    outcome: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    since_dt = _parse_dt(since)
    until_dt = _parse_dt(until)
    result: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if user is not None and event.get("user") != user:
            continue
        if session_id is not None and event.get("session_id") != session_id:
            continue
        if tool is not None and event.get("tool") != tool:
            continue
        if risk is not None and event.get("risk") != risk:
            continue
        if outcome is not None and event.get("outcome") != outcome:
            continue
        event_dt = _parse_dt(str(event.get("ts") or ""))
        if since_dt and event_dt and event_dt < since_dt:
            continue
        if until_dt and event_dt and event_dt > until_dt:
            continue
        result.append(event)
    return result


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
