from __future__ import annotations

from agentbridge.models import RiskLevel
from agentbridge.naming import snake_case

READ_ACTIONS = {"get", "list", "search", "find", "read", "fetch", "query", "view", "preview", "validate"}
WRITE_ACTIONS = {
    "create",
    "update",
    "edit",
    "rewrite",
    "write",
    "save",
    "add",
    "set",
    "assign",
    "generate",
    "import",
}
DESTRUCTIVE_ACTIONS = {
    "delete",
    "remove",
    "destroy",
    "drop",
    "purge",
    "archive",
    "deactivate",
    "cancel",
    "revoke",
}
EXTERNAL_ACTIONS = {
    "publish",
    "send",
    "email",
    "sms",
    "notify",
    "pay",
    "charge",
    "refund",
    "transfer",
    "deploy",
    "webhook",
    "export",
    "invite",
}

HTTP_READ = {"GET", "HEAD", "OPTIONS"}
HTTP_WRITE = {"POST", "PUT", "PATCH"}
HTTP_DESTRUCTIVE = {"DELETE"}


def infer_action(method: str | None = None, name: str = "", path: str = "") -> str:
    haystack = snake_case(" ".join(filter(None, [name, path]))).split("_")
    for token in haystack:
        if token in EXTERNAL_ACTIONS | DESTRUCTIVE_ACTIONS | WRITE_ACTIONS | READ_ACTIONS:
            return token
    if method:
        upper = method.upper()
        if upper in HTTP_DESTRUCTIVE:
            return "delete"
        if upper == "POST":
            return "create"
        if upper in {"PUT", "PATCH"}:
            return "update"
        if upper in HTTP_READ:
            return "list"
    return "run"


def classify_risk(action: str, method: str | None = None, path: str = "", name: str = "") -> RiskLevel:
    tokens = set(snake_case(" ".join(filter(None, [action, path, name]))).split("_"))
    if tokens & EXTERNAL_ACTIONS:
        return "external_side_effect"
    if tokens & DESTRUCTIVE_ACTIONS:
        return "destructive"
    if method and method.upper() in HTTP_DESTRUCTIVE:
        return "destructive"
    if tokens & WRITE_ACTIONS:
        return "write"
    if method and method.upper() in HTTP_WRITE:
        return "write"
    return "read"


def confirmation_required(risk: RiskLevel) -> bool:
    return risk in {"destructive", "external_side_effect"}


def risk_reason(risk: RiskLevel) -> str:
    if risk == "read":
        return "Read-only operation."
    if risk == "write":
        return "Mutates system state; dry-run should show the planned mutation."
    if risk == "destructive":
        return "May delete, cancel, remove, revoke, or otherwise destroy important state."
    return "May trigger an external side effect such as payment, publishing, email, deployment, or export."

