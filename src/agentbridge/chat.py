from __future__ import annotations

import json
import re
import shlex
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentbridge.mcp_server import AgentBridgeMCPServer, MCPServerConfig
from agentbridge.runtime import dry_run, load_capabilities, validate_args


@dataclass
class ChatConfig:
    kit_dir: Path
    base_url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    execute: bool = False
    timeout: float = 30.0
    user: str = "local"
    session_id: str = "default"
    memory_file: Path | None = None
    memory_enabled: bool = True
    max_history: int = 80


@dataclass
class PendingCall:
    id: str
    tool: str
    args: dict[str, Any]
    plan: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "tool": self.tool, "args": self.args, "plan": self.plan}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PendingCall":
        return cls(id=data["id"], tool=data["tool"], args=data.get("args", {}), plan=data.get("plan", {}))


@dataclass
class ChatResponse:
    status: str
    message: str
    tool_result: dict[str, Any] | None = None
    pending: dict[str, Any] | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "tool_result": self.tool_result,
            "pending": self.pending,
            "tools": self.tools,
            "history": self.history,
        }


class ChatMemory:
    def __init__(self, path: Path | None, enabled: bool = True, max_history: int = 80) -> None:
        self.path = path
        self.enabled = enabled and path is not None
        self.max_history = max_history

    def load(self, key: str) -> dict[str, Any]:
        if not self.enabled or not self.path or not self.path.exists():
            return {"history": [], "pending": None}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"history": [], "pending": None}
        return data.get(key, {"history": [], "pending": None})

    def save(self, key: str, state: dict[str, Any]) -> None:
        if not self.enabled or not self.path:
            return
        data: dict[str, Any] = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
        history = state.get("history", [])[-self.max_history :]
        data[key] = {"history": history, "pending": state.get("pending")}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class ChatSession:
    def __init__(self, config: ChatConfig) -> None:
        self.config = config
        self.capabilities = load_capabilities(config.kit_dir)
        self.server = AgentBridgeMCPServer(
            MCPServerConfig(
                kit_dir=config.kit_dir,
                base_url=config.base_url,
                headers=config.headers,
                execute=config.execute,
                timeout=config.timeout,
            )
        )
        memory_file = config.memory_file
        if memory_file is None and config.memory_enabled:
            memory_file = config.kit_dir / ".agentbridge-chat-memory.json"
        self.memory = ChatMemory(memory_file, enabled=config.memory_enabled, max_history=config.max_history)
        self.memory_key = f"{config.user}:{config.session_id}:{config.kit_dir.resolve()}"
        state = self.memory.load(self.memory_key)
        self.history: list[dict[str, str]] = list(state.get("history", []))
        pending_data = state.get("pending")
        self.pending: PendingCall | None = PendingCall.from_dict(pending_data) if isinstance(pending_data, dict) else None

    def process(self, message: str) -> ChatResponse:
        text = message.strip()
        if not text:
            return ChatResponse("empty", "Enter a message, /tools, /run <tool> key=value, confirm, or cancel.")
        self._remember("user", text)
        lowered = text.lower()

        if lowered in {"/help", "help", "?"}:
            return self._reply("help", self.help_text())
        if lowered in {"/tools", "tools", "list tools"}:
            tools = self.tool_summaries()
            return self._reply("tools", format_tools(tools), tools=tools)
        if lowered in {"/history", "history"}:
            return ChatResponse("history", "Session history.", history=self.history[-self.config.max_history :])
        if lowered in {"cancel", "/cancel"}:
            self.pending = None
            self._save()
            return self._reply("cancelled", "Pending operation cleared.")
        if lowered.startswith("confirm") or lowered == "/confirm":
            return self.confirm()

        parsed = parse_tool_request(text, self.capabilities)
        if not parsed:
            guidance = "I could not map that to a tool. Try /tools or /run <tool_name> key=value."
            return self._reply("not_understood", guidance)
        tool_name, args = parsed
        return self.call_tool(tool_name, args, confirmed=False)

    def call_tool(self, tool_name: str, args: dict[str, Any], confirmed: bool = False) -> ChatResponse:
        if tool_name not in self.capabilities:
            return self._reply("unknown_tool", f"Unknown tool: {tool_name}. Try /tools.")
        schema = self.capabilities[tool_name].get("input_schema", {})
        validation = validate_args(schema, args)
        if validation["errors"]:
            required = ", ".join(schema.get("required", [])) or "none"
            message = "Invalid arguments: " + "; ".join(validation["errors"]) + f". Required: {required}."
            return self._reply("invalid_arguments", message)

        plan = dry_run(self.config.kit_dir, tool_name, args, confirmed=confirmed)
        if plan["requires_confirmation"] and not confirmed:
            self.pending = PendingCall(id=str(uuid.uuid4())[:8], tool=tool_name, args=args, plan=plan)
            self._save()
            return self._reply(
                "needs_confirmation",
                format_pending_confirmation(self.pending, self.config.execute),
                pending=self.pending.to_dict(),
            )

        result = self._call_mcp(tool_name, args, confirmed=confirmed)
        message = format_tool_result(tool_name, result)
        self.pending = None
        self._save()
        return self._reply("tool_result", message, tool_result=result)

    def confirm(self) -> ChatResponse:
        if not self.pending:
            return self._reply("no_pending", "There is no pending operation to confirm.")
        pending = self.pending
        return self.call_tool(pending.tool, pending.args, confirmed=True)

    def tool_summaries(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        rules = self.server.guardrails.get("tools", {})
        for name, cap in sorted(self.capabilities.items()):
            rule = rules.get(name, {})
            result.append(
                {
                    "name": name,
                    "description": cap.get("description", name),
                    "risk": rule.get("risk", cap.get("risk", "read")),
                    "confirm_required": bool(rule.get("confirm_required", False)),
                    "required": cap.get("input_schema", {}).get("required", []),
                    "properties": sorted(cap.get("input_schema", {}).get("properties", {}).keys()),
                    "transport": rule.get("transport", cap.get("transport", {})),
                }
            )
        return result

    def help_text(self) -> str:
        return (
            "Commands:\n"
            "  /tools                         List available tools\n"
            "  /run <tool> key=value          Plan or run a tool\n"
            "  <tool> {\"arg\":\"value\"}        Run with JSON arguments\n"
            "  confirm                        Confirm the pending high-risk operation\n"
            "  cancel                         Clear the pending operation\n"
            "  /history                       Show session memory\n"
            "\n"
            "Examples:\n"
            "  /run list_chapter project_id=p1\n"
            "  create_chapter project_id=p1 title=\"Opening\"\n"
        )

    def _call_mcp(self, tool_name: str, args: dict[str, Any], confirmed: bool = False) -> dict[str, Any]:
        call_args = dict(args)
        if confirmed:
            call_args["confirmed"] = True
        response = self.server.handle(
            {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": call_args},
            }
        )
        if not response:
            return {"error": "No MCP response"}
        if "error" in response:
            return {"error": response["error"]["message"]}
        result = response.get("result", {})
        text = ""
        for item in result.get("content", []):
            if item.get("type") == "text":
                text = item.get("text", "")
                break
        try:
            payload = json.loads(text) if text else result
        except json.JSONDecodeError:
            payload = {"text": text}
        payload["is_error"] = bool(result.get("isError"))
        return payload

    def _reply(
        self,
        status: str,
        message: str,
        tool_result: dict[str, Any] | None = None,
        pending: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResponse:
        self._remember("assistant", message)
        self._save()
        return ChatResponse(
            status=status,
            message=message,
            tool_result=tool_result,
            pending=pending,
            tools=tools or [],
            history=self.history[-self.config.max_history :],
        )

    def _remember(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})
        if len(self.history) > self.config.max_history:
            self.history = self.history[-self.config.max_history :]

    def _save(self) -> None:
        pending = self.pending.to_dict() if self.pending else None
        self.memory.save(self.memory_key, {"history": self.history, "pending": pending})


def parse_tool_request(text: str, capabilities: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
    stripped = text.strip()
    if stripped.startswith("/run "):
        stripped = stripped[5:].strip()
    if stripped.startswith("run "):
        stripped = stripped[4:].strip()

    json_args = extract_json_object(stripped)
    without_json = stripped
    if json_args is not None:
        start = stripped.find("{")
        without_json = stripped[:start].strip()

    tokens = shlex.split(without_json) if without_json else []
    tool_name = ""
    if tokens and tokens[0] in capabilities:
        tool_name = tokens[0]
        arg_text = " ".join(tokens[1:])
    else:
        tool_name = match_tool(stripped, capabilities)
        arg_text = stripped
    if not tool_name:
        return None

    args = dict(json_args or {})
    args.update(parse_key_values(arg_text))
    args.update(parse_named_values(stripped, capabilities[tool_name].get("input_schema", {})))
    return tool_name, coerce_args(args, capabilities[tool_name].get("input_schema", {}))


def extract_json_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        return None


def parse_key_values(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = text.split()
    for token in tokens:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if key:
            result[key.strip()] = parse_scalar(value.strip())
    return result


def parse_named_values(text: str, schema: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in schema.get("properties", {}):
        if key in result:
            continue
        pattern = re.compile(rf"\b{re.escape(key)}\b\s*[:=]\s*(\"[^\"]+\"|'[^']+'|[^\s,]+)", re.I)
        match = pattern.search(text)
        if match:
            result[key] = parse_scalar(match.group(1).strip("\"'"))
    return result


def parse_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def coerce_args(args: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    coerced = dict(args)
    properties = schema.get("properties", {})
    for key, value in list(coerced.items()):
        expected = properties.get(key, {}).get("type") if isinstance(properties.get(key), dict) else None
        if expected == "string" and not isinstance(value, str):
            coerced[key] = str(value)
        elif expected in {"number", "integer"} and isinstance(value, str):
            try:
                coerced[key] = float(value) if expected == "number" else int(value)
            except ValueError:
                pass
        elif expected == "boolean" and isinstance(value, str):
            coerced[key] = parse_scalar(value)
    return coerced


def match_tool(text: str, capabilities: dict[str, dict[str, Any]]) -> str:
    lowered = text.lower()
    best_name = ""
    best_score = 0
    for name, cap in capabilities.items():
        score = 0
        if name in lowered:
            score += 10
        words = set(re.split(r"[_\W]+", name.lower()))
        words.update([str(cap.get("action", "")).lower(), str(cap.get("resource", "")).lower()])
        for word in words:
            if word and word in lowered:
                score += 1
        if score > best_score:
            best_name = name
            best_score = score
    return best_name if best_score >= 2 else ""


def format_tools(tools: list[dict[str, Any]]) -> str:
    lines = ["Available tools:"]
    for tool in tools:
        required = ", ".join(tool.get("required", [])) or "none"
        confirm = " confirm required" if tool.get("confirm_required") else ""
        lines.append(f"- {tool['name']} [{tool['risk']}]{confirm}: required {required}")
    return "\n".join(lines)


def format_pending_confirmation(pending: PendingCall, execute: bool) -> str:
    plan = pending.plan
    transport = plan.get("transport", {})
    mode = "execute" if execute else "dry-run"
    return (
        f"Confirmation required for `{pending.tool}` ({plan.get('risk')}, {mode}).\n"
        f"Planned call: {transport.get('method', transport.get('type', 'unknown'))} {transport.get('path', '')}\n"
        f"Arguments: {json.dumps(pending.args, sort_keys=True)}\n"
        "Type `confirm` to continue or `cancel` to stop."
    )


def format_tool_result(tool_name: str, result: dict[str, Any]) -> str:
    if result.get("error"):
        return f"{tool_name} failed: {result['error']}"
    if result.get("status") == "executed":
        response = result.get("response", {})
        return f"{tool_name} executed. HTTP {response.get('status')}."
    if "would_execute" in result:
        next_step = result.get("next_step", "")
        return f"{tool_name} planned. {next_step}"
    return f"{tool_name} completed."
