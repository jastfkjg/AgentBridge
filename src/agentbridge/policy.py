from __future__ import annotations

from typing import Any

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

DEFAULT_RISK_ACTIONS = {
    "read": "allow",
    "write": "confirm",
    "destructive": "deny",
    "external_side_effect": "confirm",
}

DEFAULT_POLICY = {
    "human_in_the_loop": {
        "read": "auto_execute",
        "write": "confirm",
        "destructive": "deny",
        "external_side_effect": "confirm",
    },
    "risk_actions": DEFAULT_RISK_ACTIONS,
    "confirmation": {
        "write": "required",
        "external_side_effect": "required",
    },
    "redaction": {
        "sensitive_key_markers": [
            "authorization",
            "cookie",
            "password",
            "token",
            "secret",
            "api_key",
        ]
    },
}


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


def default_policy() -> dict[str, Any]:
    return {
        "human_in_the_loop": dict(DEFAULT_POLICY["human_in_the_loop"]),
        "risk_actions": dict(DEFAULT_POLICY["risk_actions"]),
        "confirmation": dict(DEFAULT_POLICY["confirmation"]),
        "redaction": {
            "sensitive_key_markers": list(DEFAULT_POLICY["redaction"]["sensitive_key_markers"])
        },
    }


def normalize_permissions_policy(guardrails: dict[str, Any] | None) -> dict[str, Any]:
    source = guardrails if isinstance(guardrails, dict) else {}
    raw_policy = source.get("policy", {})
    policy = default_policy()
    if isinstance(raw_policy, dict):
        for key in ("human_in_the_loop", "risk_actions", "confirmation"):
            value = raw_policy.get(key)
            if isinstance(value, dict):
                merged = dict(policy.get(key, {}))
                merged.update({str(item_key): str(item_value) for item_key, item_value in value.items()})
                policy[key] = merged
        redaction = raw_policy.get("redaction")
        if isinstance(redaction, dict):
            markers = redaction.get("sensitive_key_markers")
            if isinstance(markers, list):
                policy["redaction"] = {"sensitive_key_markers": [str(item) for item in markers]}
    return policy


def risk_policy_action(guardrails: dict[str, Any] | None, risk: str) -> str:
    policy = normalize_permissions_policy(guardrails)
    action = str(policy.get("risk_actions", {}).get(risk, DEFAULT_RISK_ACTIONS.get(risk, "confirm")))
    return action if action in {"allow", "confirm", "deny"} else "confirm"


def risk_requires_confirmation(guardrails: dict[str, Any] | None, risk: str, rule_confirm_required: bool = False) -> bool:
    return rule_confirm_required or risk_policy_action(guardrails, risk) == "confirm"


def risk_is_denied(guardrails: dict[str, Any] | None, risk: str) -> bool:
    return risk_policy_action(guardrails, risk) == "deny"
