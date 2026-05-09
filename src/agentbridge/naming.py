from __future__ import annotations

import re

_STOP_WORDS = {
    "api",
    "v1",
    "v2",
    "by",
    "id",
    "get",
    "post",
    "put",
    "patch",
    "delete",
}

_ACTION_SEGMENTS = {
    "archive",
    "cancel",
    "charge",
    "delete",
    "deploy",
    "email",
    "export",
    "import",
    "invite",
    "notify",
    "pay",
    "publish",
    "refund",
    "rewrite",
    "send",
    "sms",
    "transfer",
    "webhook",
}


def snake_case(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"[^a-zA-Z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_").lower()
    return value or "capability"


def singular(value: str) -> str:
    value = value.strip("_/{} ").lower()
    if value.endswith("ies") and len(value) > 3:
        return value[:-3] + "y"
    if value.endswith("ses"):
        return value[:-2]
    if value.endswith("s") and not value.endswith("ss") and len(value) > 1:
        return value[:-1]
    return value


def humanize(value: str) -> str:
    return snake_case(value).replace("_", " ")


def resource_from_path(path: str) -> str:
    segments = [segment for segment in path.split("/") if segment]
    literal_segments = [
        segment
        for segment in segments
        if not segment.startswith("{") and not segment.endswith("}") and not segment.startswith(":")
    ]
    parts = [p for segment in literal_segments for p in re.split(r"[._-]+", segment) if p]
    parts = [p for p in parts if p.lower() not in _STOP_WORDS]
    if not parts:
        return "resource"
    if len(parts) > 1 and parts[-1].lower() in _ACTION_SEGMENTS:
        return singular(parts[-2])
    return singular(parts[-1])


def domain_from_resource(resource: str) -> str:
    resource = singular(snake_case(resource))
    groups = {
        "writing": {"chapter", "character", "scene", "outline", "draft", "manuscript", "story", "novel"},
        "commerce": {"order", "payment", "invoice", "refund", "product", "cart"},
        "publishing": {"post", "article", "page", "comment", "asset", "publication"},
        "identity": {"user", "role", "permission", "profile", "account", "session"},
        "notification": {"email", "message", "sms", "notification", "webhook"},
    }
    for domain, resources in groups.items():
        if resource in resources:
            return domain
    return resource


def capability_name(action: str, resource: str) -> str:
    return snake_case(f"{action}_{singular(resource)}")
