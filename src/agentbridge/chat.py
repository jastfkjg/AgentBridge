from __future__ import annotations

import json
import os
import re
import shlex
import threading
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from agentbridge.adapters import AdapterRuntimeConfig, build_request_preview
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
    read_only: bool = False
    deny_risks: set[str] = field(default_factory=set)
    allow_tools: set[str] = field(default_factory=set)
    audit_log: Path | None = None
    agent_enabled: bool = True
    agent_runner: Any | None = field(default=None, repr=False)
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_timeout: float | None = None
    graphql_endpoint: str = ""
    database_url: str = ""
    grpc_target: str = ""


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
class ChatEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **self.data}


@dataclass
class ChatResponse:
    status: str
    message: str
    tool_result: dict[str, Any] | None = None
    pending: dict[str, Any] | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "tool_result": self.tool_result,
            "pending": self.pending,
            "tools": self.tools,
            "history": self.history,
            "usage": self.usage,
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
        data[key] = {
            "history": history,
            "pending": state.get("pending"),
            "usage": state.get("usage", {}),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class ChatSession:
    def __init__(self, config: ChatConfig) -> None:
        self.config = config
        self.capabilities = load_capabilities(config.kit_dir)
        self.server = self._build_server(config)
        memory_file = config.memory_file
        if memory_file is None and config.memory_enabled:
            memory_file = config.kit_dir / ".agentbridge-chat-memory.json"
        self.memory = ChatMemory(memory_file, enabled=config.memory_enabled, max_history=config.max_history)
        self.memory_key = f"{config.user}:{config.session_id}:{config.kit_dir.resolve()}"
        state = self.memory.load(self.memory_key)
        self.history: list[dict[str, str]] = list(state.get("history", []))
        self.usage: dict[str, Any] = dict(state.get("usage", {}))
        pending_data = state.get("pending")
        self.pending: PendingCall | None = PendingCall.from_dict(pending_data) if isinstance(pending_data, dict) else None
        self.agent_runner = config.agent_runner
        self._agent_runner_supplied = config.agent_runner is not None
        self._active_request_id = ""
        self._active_cancel = threading.Event()

    @staticmethod
    def _build_server(config: ChatConfig) -> AgentBridgeMCPServer:
        return AgentBridgeMCPServer(
            MCPServerConfig(
                kit_dir=config.kit_dir,
                base_url=config.base_url,
                headers=config.headers,
                execute=config.execute,
                timeout=config.timeout,
                read_only=config.read_only,
                deny_risks=config.deny_risks,
                allow_tools=config.allow_tools,
                audit_log=config.audit_log,
                graphql_endpoint=config.graphql_endpoint,
                database_url=config.database_url,
                grpc_target=config.grpc_target,
            )
        )

    def update_runtime(self, base_url: str, execute: bool) -> None:
        if self.config.base_url == base_url and self.config.execute == execute:
            return
        self.pending = None
        self.config = replace(self.config, base_url=base_url, execute=execute)
        self.server = self._build_server(self.config)
        if not self._agent_runner_supplied:
            self._close_agent_runner()
            self.agent_runner = None
        self._save()

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
            if text.startswith("/"):
                guidance = "Unknown command. Try /tools, /run <tool_name> key=value, confirm, or cancel."
                return self._reply("unknown_command", guidance)
            return self._agent_reply(text)
        tool_name, args = parsed
        return self.call_tool(tool_name, args, confirmed=False)

    def stream_process(self, message: str) -> Any:
        text = message.strip()
        if not text:
            yield ChatEvent("error", {"message": "Enter a message, /tools, /run <tool> key=value, confirm, or cancel."})
            return
        lowered = text.lower()
        parsed = parse_tool_request(text, self.capabilities)
        should_stream_agent = (
            not parsed
            and not text.startswith("/")
            and lowered not in {"/help", "help", "?", "/tools", "tools", "list tools", "/history", "history", "cancel", "/cancel", "confirm", "/confirm"}
            and not lowered.startswith("confirm")
        )
        if should_stream_agent:
            yield from self._stream_agent_reply(text)
            return
        response = self.process(message)
        yield from self._events_from_response(response)

    def interrupt(self) -> ChatResponse:
        self._active_cancel.set()
        runner = self.agent_runner
        interrupt = getattr(runner, "interrupt", None)
        if callable(interrupt):
            try:
                interrupt()
            except Exception:
                pass
        close = getattr(runner, "close", None)
        if callable(close) and not self._agent_runner_supplied:
            try:
                close()
            except Exception:
                pass
            self.agent_runner = None
        return self._reply("interrupted", "Current Agent request interrupted.")

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
        try:
            plan["request_preview"] = build_request_preview(
                capability=self.capabilities[tool_name],
                args=args,
                config=AdapterRuntimeConfig(
                    base_url=self.config.base_url,
                    headers=self.config.headers,
                    timeout=self.config.timeout,
                    graphql_endpoint=self.config.graphql_endpoint,
                    database_url=self.config.database_url,
                    grpc_target=self.config.grpc_target,
                ),
            )
        except Exception as exc:
            plan["request_preview"] = {"error": str(exc)}
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
            input_schema = cap.get("input_schema", {})
            property_schemas = input_schema.get("properties", {})
            result.append(
                {
                    "name": name,
                    "description": cap.get("description", name),
                    "risk": rule.get("risk", cap.get("risk", "read")),
                    "confirm_required": bool(rule.get("confirm_required", False)),
                    "required": input_schema.get("required", []),
                    "properties": sorted(property_schemas),
                    "property_schemas": property_schemas,
                    "transport": rule.get("transport", cap.get("transport", {})),
                }
            )
        return result

    def help_text(self) -> str:
        return (
            "Commands for controlling the parsed system tool layer:\n"
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

    def _agent_reply(self, text: str) -> ChatResponse:
        if not self.config.agent_enabled:
            return self._reply("agent_unavailable", agent_unavailable_message())
        runner = self._get_agent_runner()
        if runner is None:
            return self._reply("agent_unavailable", agent_unavailable_message())
        try:
            self._active_request_id = str(uuid.uuid4())
            self._active_cancel.clear()
            if hasattr(runner, "query_text"):
                message = str(runner.query_text(text)).strip()
            else:
                message = ""
            if self._active_cancel.is_set():
                return self._reply("interrupted", "Current Agent request interrupted.")
            current_usage = getattr(runner, "last_usage", {})
            if isinstance(current_usage, dict) and current_usage:
                self._record_usage(current_usage)
            if not message:
                message = "The AI agent did not return a response."
            return self._reply("agent_response", message)
        except Exception as exc:
            return self._reply("agent_error", f"AI agent request failed: {exc}")
        finally:
            self._active_request_id = ""

    def _stream_agent_reply(self, text: str) -> Any:
        self._remember("user", text)
        if not self.config.agent_enabled:
            response = self._reply("agent_unavailable", agent_unavailable_message())
            yield from self._events_from_response(response)
            return
        runner = self._get_agent_runner()
        if runner is None:
            response = self._reply("agent_unavailable", agent_unavailable_message())
            yield from self._events_from_response(response)
            return
        request_id = str(uuid.uuid4())
        self._active_request_id = request_id
        self._active_cancel.clear()
        yield ChatEvent("message_start", {"request_id": request_id, "model": getattr(runner, "model", self.config.llm_model or "")})
        chunks: list[str] = []
        try:
            if hasattr(runner, "query_messages"):
                messages = runner.query_messages(text)
            elif hasattr(runner, "query_text"):
                messages = [str(runner.query_text(text))]
            else:
                messages = []
            saw_message_usage = False
            for message in messages:
                if self._active_cancel.is_set():
                    yield ChatEvent("interrupted", {"message": "Current Agent request interrupted."})
                    return
                for event in self._events_from_agent_message(message):
                    if event.type == "usage":
                        saw_message_usage = True
                    if event.type in {"assistant_text", "assistant_text_delta"}:
                        content = str(event.data.get("text", ""))
                        if content:
                            chunks.append(content)
                    yield event
            current_usage = getattr(runner, "last_usage", {})
            if isinstance(current_usage, dict) and current_usage and not saw_message_usage:
                self._record_usage(current_usage)
            final = "\n".join(chunk for chunk in chunks if chunk).strip() or "The AI agent did not return a response."
            self._remember("assistant", final)
            self._save()
            yield ChatEvent("usage", {"usage": dict(self.usage)})
            yield ChatEvent("done", {"status": "agent_response", "message": final, "usage": dict(self.usage)})
        except Exception as exc:
            message = f"AI agent request failed: {exc}"
            self._remember("assistant", message)
            self._save()
            yield ChatEvent("error", {"message": message})
        finally:
            self._active_request_id = ""

    def _get_agent_runner(self) -> Any | None:
        if self.agent_runner is not None:
            return self.agent_runner
        api_key = self.config.llm_api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None
        try:
            from agentbridge.agent import AgentRunner

            self.agent_runner = AgentRunner(
                self.config.kit_dir,
                api_key=api_key,
                base_url=self.config.llm_base_url or None,
                model=self.config.llm_model or None,
                target_base_url=self.config.base_url,
                headers=self.config.headers,
                execute=self.config.execute,
                timeout=self.config.timeout,
                read_only=self.config.read_only,
                deny_risks=self.config.deny_risks,
                allow_tools=self.config.allow_tools,
                audit_log=self.config.audit_log,
                session_id=self.config.session_id,
                llm_timeout=self.config.llm_timeout,
                graphql_endpoint=self.config.graphql_endpoint,
                database_url=self.config.database_url,
                grpc_target=self.config.grpc_target,
            )
            return self.agent_runner
        except Exception:
            return None

    def _close_agent_runner(self) -> None:
        runner = self.agent_runner
        close = getattr(runner, "close", None)
        if callable(close):
            close()

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
            usage=dict(self.usage),
        )

    def _remember(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})
        if len(self.history) > self.config.max_history:
            self.history = self.history[-self.config.max_history :]

    def _save(self) -> None:
        pending = self.pending.to_dict() if self.pending else None
        self.memory.save(
            self.memory_key,
            {"history": self.history, "pending": pending, "usage": self.usage},
        )

    def _record_usage(self, usage: dict[str, Any]) -> None:
        for key in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            value = int(usage.get(key, 0) or 0)
            if value:
                session_key = f"session_{key}"
                self.usage[session_key] = int(self.usage.get(session_key, 0) or 0) + value
                self.usage[key] = value
        for key in ("cost_usd", "duration_ms", "turns"):
            if key in usage:
                self.usage[key] = usage[key]
        if "cost_usd" in usage:
            self.usage["session_cost_usd"] = float(self.usage.get("session_cost_usd", 0) or 0) + float(usage["cost_usd"])

    def _events_from_response(self, response: ChatResponse) -> list[ChatEvent]:
        events: list[ChatEvent] = [ChatEvent("message_start", {"status": response.status})]
        if response.status == "needs_confirmation":
            events.append(ChatEvent("confirmation_required", {"pending": response.pending, "message": response.message}))
        elif response.status == "tool_result":
            events.append(ChatEvent("tool_result", {"result": response.tool_result, "message": response.message}))
        elif response.tools:
            events.append(ChatEvent("tools", {"tools": response.tools, "message": response.message}))
        else:
            events.append(ChatEvent("assistant_text", {"text": response.message}))
        if response.usage:
            events.append(ChatEvent("usage", {"usage": response.usage}))
        events.append(ChatEvent("done", response.to_dict()))
        return events

    def _events_from_agent_message(self, message: Any) -> list[ChatEvent]:
        from agentbridge.agent import _agent_sdk_progress_events, _extract_agent_message_text, _extract_agent_result_text, _extract_agent_usage

        events: list[ChatEvent] = []
        for block in _message_content_blocks(message):
            block_type = _content_block_type(block)
            if block_type == "tool_use" or block_type.endswith("ToolUseBlock"):
                events.append(
                    ChatEvent(
                        "tool_use",
                        {
                            "id": _content_block_value(block, "id") or _content_block_value(block, "tool_use_id"),
                            "name": _content_block_value(block, "name") or _content_block_value(block, "tool_name") or "tool",
                            "input": _content_block_value(block, "input") or {},
                        },
                    )
                )
            elif block_type == "tool_result" or block_type.endswith("ToolResultBlock"):
                events.append(
                    ChatEvent(
                        "tool_result",
                        {
                            "tool_use_id": _content_block_value(block, "tool_use_id"),
                            "content": _content_block_value(block, "content") or _content_block_value(block, "result") or _content_block_value(block, "text"),
                            "is_error": bool(_content_block_value(block, "is_error") or _content_block_value(block, "isError")),
                        },
                    )
                )
            elif "thinking" in block_type.lower() or "reasoning" in block_type.lower():
                events.append(ChatEvent("thinking", {"message": "Claude Agent SDK internal reasoning step completed."}))
        result_text = _extract_agent_result_text(message)
        text = result_text or _extract_agent_message_text(message)
        if text:
            events.append(ChatEvent("assistant_text", {"text": text}))
        usage = _extract_agent_usage(message)
        if usage:
            self._record_usage(usage)
            events.append(ChatEvent("usage", {"usage": dict(self.usage)}))
        for progress in _agent_sdk_progress_events(message):
            events.append(ChatEvent("timeline", {"message": progress}))
        return events


def _message_content_blocks(message: Any) -> list[Any]:
    if isinstance(message, str):
        return []
    if isinstance(message, dict):
        content = message.get("content", [])
    else:
        content = getattr(message, "content", [])
    if content is None:
        return []
    if isinstance(content, list):
        return content
    if isinstance(content, tuple):
        return list(content)
    if isinstance(content, str):
        return []
    return [content]


def _content_block_type(block: Any) -> str:
    if isinstance(block, dict):
        return str(block.get("type", ""))
    value = getattr(block, "type", None)
    if isinstance(value, str):
        return value
    return block.__class__.__name__


def _content_block_value(block: Any, key: str) -> Any:
    if isinstance(block, dict):
        return block.get(key)
    return getattr(block, key, None)


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
    preview = plan.get("request_preview", {})
    mode = "execute" if execute else "dry-run"
    target = f"{preview.get('method', transport.get('method', transport.get('type', 'unknown')))} {preview.get('url', transport.get('path', ''))}"
    body = preview.get("body")
    headers = preview.get("headers")
    return (
        f"Confirmation required for `{pending.tool}` ({plan.get('risk')}, {mode}).\n"
        f"Reason: {plan.get('risk_reason') or 'Review this operation before continuing.'}\n"
        f"Planned call: {target}\n"
        f"Headers: {json.dumps(headers, sort_keys=True) if headers else '{}'}\n"
        f"Body: {json.dumps(body, sort_keys=True) if body is not None else 'null'}\n"
        f"Arguments: {json.dumps(pending.args, sort_keys=True)}\n"
        "Type `confirm` to continue or `cancel` to stop."
    )


def format_tool_result(tool_name: str, result: dict[str, Any]) -> str:
    if result.get("policy_error"):
        return f"{tool_name} blocked by runtime policy: {result['policy_error']}"
    if result.get("error"):
        return f"{tool_name} failed: {result['error']}"
    if result.get("status") == "executed":
        response = result.get("response", {})
        return f"{tool_name} executed. HTTP {response.get('status')}."
    if "would_execute" in result:
        next_step = result.get("next_step", "")
        return f"{tool_name} planned. {next_step}"
    return f"{tool_name} completed."


def agent_unavailable_message() -> str:
    return (
        "AI agent chat is not configured. Set ANTHROPIC_API_KEY and install the Claude Agent SDK "
        "with `pip install \"agbr[agent]\"`, then restart `agentbridge web`."
    )
