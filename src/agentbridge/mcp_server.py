from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from agentbridge.runtime import DryRunError, dry_run, load_capabilities, validate_args

_PATH_PARAM_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}|:([A-Za-z_][A-Za-z0-9_]*)")
_SENSITIVE_HEADER_NAMES = {"authorization", "cookie", "proxy-authorization", "x-api-key", "api-key"}


@dataclass
class MCPServerConfig:
    kit_dir: Path
    base_url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    execute: bool = False
    timeout: float = 30.0
    read_only: bool = False
    deny_risks: set[str] = field(default_factory=set)
    allow_tools: set[str] = field(default_factory=set)
    audit_log: Path | None = None


class MCPServerError(ValueError):
    pass


class AgentBridgeMCPServer:
    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.capabilities = load_capabilities(config.kit_dir)
        guardrail_path = config.kit_dir / "guardrails" / "permissions.json"
        self.guardrails = json.loads(guardrail_path.read_text(encoding="utf-8"))

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})
        try:
            if method == "initialize":
                return self._response(request_id, self._initialize_result())
            if method == "tools/list":
                return self._response(request_id, {"tools": self._tools()})
            if method == "tools/call":
                return self._response(request_id, self._call_tool(params))
            if method in {"notifications/initialized", "initialized"}:
                return None
            return self._error(request_id, -32601, f"Unsupported MCP method: {method}")
        except Exception as exc:
            return self._error(request_id, -32000, str(exc))

    def _initialize_result(self) -> dict[str, Any]:
        return {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "agentbridge", "version": "0.1.0"},
            "capabilities": {"tools": {}},
        }

    def _tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        rules = self.guardrails.get("tools", {})
        for name, cap in sorted(self.capabilities.items()):
            schema = dict(cap.get("input_schema", {"type": "object", "properties": {}}))
            schema["properties"] = dict(schema.get("properties", {}))
            rule = rules.get(name, {})
            if rule.get("confirm_required"):
                schema["properties"]["confirmed"] = {
                    "type": "boolean",
                    "description": "Set true only after the user explicitly confirms this high-risk operation.",
                }
            tools.append(
                {
                    "name": name,
                    "description": cap.get("description", name),
                    "inputSchema": schema,
                    "annotations": {
                        "risk": rule.get("risk", cap.get("risk", "read")),
                        "confirm_required": bool(rule.get("confirm_required", False)),
                        "execution_mode": "execute" if self.config.execute else "dry_run",
                    },
                }
            )
        return tools

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise MCPServerError("tools/call requires params.name")
        raw_args = params.get("arguments", {})
        if raw_args is None:
            raw_args = {}
        if not isinstance(raw_args, dict):
            raise MCPServerError("tools/call params.arguments must be an object")

        args = dict(raw_args)
        confirmed = bool(args.pop("confirmed", False))
        plan = dry_run(self.config.kit_dir, name, args, confirmed=confirmed)
        capability = self.capabilities[name]
        self._attach_request_preview(plan, capability, args)
        policy_error = self._policy_error(name, plan)
        if policy_error:
            plan = dict(plan)
            plan["allowed"] = False
            plan["policy_error"] = policy_error
            self._audit(name, args, "blocked", plan)
            return _tool_text(plan, is_error=True)

        if not self.config.execute:
            self._audit(name, args, "dry_run", plan)
            return _tool_text(plan, is_error=False)
        if not plan["allowed"]:
            self._audit(name, args, "blocked", plan)
            return _tool_text(plan, is_error=True)

        transport = plan.get("transport", {})
        if transport.get("type") != "http":
            raise MCPServerError(f"Execution is only implemented for HTTP tools, got: {transport.get('type', 'unknown')}")
        result = execute_http_tool(
            capability=capability,
            args=args,
            base_url=self.config.base_url,
            headers=self.config.headers,
            timeout=self.config.timeout,
        )
        self._audit(name, args, "executed" if not result.get("error") else "error", result)
        return _tool_text(result, is_error=bool(result.get("error")))

    def _policy_error(self, name: str, plan: dict[str, Any]) -> str:
        risk = str(plan.get("risk", "read"))
        if self.config.allow_tools and name not in self.config.allow_tools:
            return f"Tool {name} is not in the allowlist."
        if risk in self.config.deny_risks:
            return f"Risk level {risk} is disabled by runtime policy."
        if self.config.read_only and risk != "read":
            return f"Read-only mode blocks {risk} tool {name}."
        return ""

    def _audit(self, name: str, args: dict[str, Any], outcome: str, payload: dict[str, Any]) -> None:
        if not self.config.audit_log:
            return
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": name,
            "outcome": outcome,
            "risk": payload.get("risk"),
            "execute": self.config.execute,
            "read_only": self.config.read_only,
            "args": args,
            "transport": payload.get("request_preview") or payload.get("transport") or payload.get("request"),
            "error": payload.get("error") or payload.get("policy_error"),
        }
        self.config.audit_log.parent.mkdir(parents=True, exist_ok=True)
        with self.config.audit_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")

    def _attach_request_preview(self, plan: dict[str, Any], capability: dict[str, Any], args: dict[str, Any]) -> None:
        if plan.get("transport", {}).get("type") != "http":
            return
        try:
            plan["request_preview"] = build_http_request_preview(
                capability=capability,
                args=args,
                base_url=self.config.base_url,
                headers=self.config.headers,
            )
        except MCPServerError as exc:
            plan["request_preview"] = {"error": str(exc)}

    def _response(self, request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _error(self, request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def execute_http_tool(
    capability: dict[str, Any],
    args: dict[str, Any],
    base_url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    if not base_url:
        raise MCPServerError("--base-url is required when --execute is enabled")

    validation = validate_args(capability.get("input_schema", {}), args)
    if validation["errors"]:
        raise MCPServerError("; ".join(validation["errors"]))

    transport = capability.get("transport", {})
    method = str(transport.get("method", "GET")).upper()
    path = str(transport.get("path", ""))
    if not path:
        raise MCPServerError(f"HTTP tool {capability.get('name', '')} is missing transport.path")

    url, remaining_args = build_http_url(base_url, path, args)
    data: bytes | None = None
    request_headers = dict(headers or {})
    if method not in {"GET", "HEAD", "OPTIONS"} and remaining_args:
        data = json.dumps(remaining_args).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    elif method in {"GET", "HEAD", "OPTIONS"} and remaining_args:
        url = append_query(url, remaining_args)

    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read()
            return {
                "tool": capability.get("name", ""),
                "status": "executed",
                "request": {"method": method, "url": url, "body": remaining_args if data else None},
                "response": format_http_response(response.status, dict(response.headers), body),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return {
            "tool": capability.get("name", ""),
            "status": "http_error",
            "error": f"HTTP {exc.code}",
            "request": {"method": method, "url": url, "body": remaining_args if data else None},
            "response": format_http_response(exc.code, dict(exc.headers), body),
        }
    except urllib.error.URLError as exc:
        raise MCPServerError(f"HTTP request failed: {exc.reason}") from exc


def build_http_request_preview(
    capability: dict[str, Any],
    args: dict[str, Any],
    base_url: str = "",
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    transport = capability.get("transport", {})
    method = str(transport.get("method", "GET")).upper()
    path = str(transport.get("path", ""))
    if not path:
        raise MCPServerError(f"HTTP tool {capability.get('name', '')} is missing transport.path")
    url, remaining_args = build_http_url(base_url, path, args)
    body: dict[str, Any] | None = None
    if method in {"GET", "HEAD", "OPTIONS"}:
        url = append_query(url, remaining_args)
    elif remaining_args:
        body = remaining_args
    return {
        "method": method,
        "url": url,
        "headers": redact_headers(headers or {}),
        "body": body,
    }


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in _SENSITIVE_HEADER_NAMES:
            redacted[key] = "<redacted>"
        else:
            redacted[key] = value
    return redacted


def build_http_url(base_url: str, path: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    remaining = dict(args)

    def replace(match: re.Match[str]) -> str:
        key = match.group(1) or match.group(2)
        if key not in remaining:
            raise MCPServerError(f"Missing path argument: {key}")
        value = remaining.pop(key)
        return urllib.parse.quote(str(value), safe="")

    rendered_path = _PATH_PARAM_PATTERN.sub(replace, path)
    return f"{base_url.rstrip('/')}/{rendered_path.lstrip('/')}", remaining


def append_query(url: str, query_args: dict[str, Any]) -> str:
    if not query_args:
        return url
    separator = "&" if urllib.parse.urlparse(url).query else "?"
    return f"{url}{separator}{urllib.parse.urlencode(query_args, doseq=True)}"


def format_http_response(status: int, headers: dict[str, str], body: bytes) -> dict[str, Any]:
    text = body.decode("utf-8", errors="replace")
    parsed: Any = None
    if text:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = text
    return {"status": status, "headers": headers, "body": parsed}


def run_stdio_server(
    config: MCPServerConfig,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    server = AgentBridgeMCPServer(config)
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    for line in input_stream:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
        else:
            response = server.handle(request)
        if response is None:
            continue
        output_stream.write(json.dumps(response, separators=(",", ":")) + "\n")
        output_stream.flush()
    return 0


def _tool_text(payload: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True)}],
        "isError": is_error,
    }
