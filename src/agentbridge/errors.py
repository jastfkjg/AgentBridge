from __future__ import annotations

from typing import Any


def structured_error(
    code: str,
    message: str,
    category: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "category": category,
    }
    if details:
        error["details"] = details
    return error


def error_code_for_exception(message: str) -> str:
    lowered = message.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    if "permission" in lowered or "policy" in lowered or "blocked" in lowered:
        return "permission_denied"
    if "schema" in lowered or "argument" in lowered or "required" in lowered:
        return "schema_mismatch"
    return "adapter_error"
