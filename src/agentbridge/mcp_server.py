from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from agentbridge.adapters import (
    AdapterError,
    AdapterRuntimeConfig,
    append_query,
    build_http_request_preview,
    build_http_url,
    build_request_preview,
    capture_auth_headers_from_result,
    execute_http_tool,
    execute_tool,
    format_http_response,
    redact_headers,
)
from agentbridge.runtime import dry_run, load_runtime_kit


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
    graphql_endpoint: str = ""
    database_url: str = ""
    grpc_target: str = ""


class MCPServerError(ValueError):
    pass


class AgentBridgeMCPServer:
    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.capabilities, self.guardrails = load_runtime_kit(config.kit_dir)

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

        try:
            result = execute_tool(capability, args, self._adapter_config())
        except AdapterError as exc:
            raise MCPServerError(str(exc)) from exc
        capture_auth_headers_from_result(result, self.config.headers)
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
        try:
            plan["request_preview"] = build_request_preview(capability, args, self._adapter_config())
        except AdapterError as exc:
            plan["request_preview"] = {"error": str(exc)}

    def _adapter_config(self) -> AdapterRuntimeConfig:
        return AdapterRuntimeConfig(
            base_url=self.config.base_url,
            headers=self.config.headers,
            timeout=self.config.timeout,
            graphql_endpoint=self.config.graphql_endpoint,
            database_url=self.config.database_url,
            grpc_target=self.config.grpc_target,
        )

    def _response(self, request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _error(self, request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


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
