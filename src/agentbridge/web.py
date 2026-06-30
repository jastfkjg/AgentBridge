from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen as url_open

from agentbridge.chat import ChatConfig, ChatSession, public_login_accounts


class ChatWebError(ValueError):
    pass


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request: Any, client_address: Any) -> None:
        exc_type, exc, _traceback = sys.exc_info()
        if exc_type in {BrokenPipeError, ConnectionResetError} or isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


SECRET_LOG_KEYS = {"password", "passwd", "pwd", "authorization", "cookie", "token", "secret", "api_key", "x-api-key"}


def web_log(event: str, **fields: Any) -> None:
    payload = redact_for_log(fields)
    suffix = " " + json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload else ""
    print(f"[web] {event}{suffix}", flush=True)


def redact_for_log(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in SECRET_LOG_KEYS or any(marker in lowered for marker in ["password", "token", "secret"]):
                redacted[str(key)] = "<redacted>"
            else:
                redacted[str(key)] = redact_for_log(item)
        return redacted
    if isinstance(value, list):
        return [redact_for_log(item) for item in value]
    return value


def normalize_target_base_url(value: str) -> str:
    base_url = value.strip().rstrip("/")
    if not base_url:
        raise ChatWebError("Base URL is required in real system mode.")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise ChatWebError("Base URL must start with http:// or https://.")
    if not parsed.netloc:
        raise ChatWebError("Base URL must include a host.")
    return base_url


def test_target_connectivity(
    base_url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    target = normalize_target_base_url(base_url)
    started = time.monotonic()
    request_headers = dict(headers or {})
    for method in ("HEAD", "GET"):
        request = Request(target, headers=request_headers, method=method)
        try:
            with url_open(request, timeout=timeout) as response:
                return {
                    "reachable": True,
                    "base_url": target,
                    "method": method,
                    "status": response.status,
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                }
        except HTTPError as exc:
            if exc.code == HTTPStatus.METHOD_NOT_ALLOWED and method == "HEAD":
                exc.close()
                continue
            result = {
                "reachable": True,
                "base_url": target,
                "method": method,
                "status": exc.code,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            }
            exc.close()
            return result
        except URLError as exc:
            return {
                "reachable": False,
                "base_url": target,
                "method": method,
                "error": str(exc.reason),
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            }
    return {
        "reachable": False,
        "base_url": target,
        "method": "GET",
        "error": "The target did not accept HEAD or GET.",
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }


def runtime_payload(session: ChatSession) -> dict[str, Any]:
    return {
        "execute": session.config.execute,
        "base_url": session.config.base_url,
        "login_accounts": public_login_accounts(session.runtime_state),
        "selected_login_account": str(session.runtime_state.get("selected_login_account") or ""),
    }


def rename_conversation(config: ChatConfig, user: str, kit_dir: Path, session_id: str, title: str) -> None:
    title = title.strip()
    if not title:
        raise ChatWebError("Conversation title is required.")
    path = memory_path_for(config, kit_dir)
    if not path or not path.exists():
        raise ChatWebError("Conversation memory was not found.")
    data = _load_memory_file(path)
    key = f"{user}:{session_id}:{kit_dir.resolve()}"
    state = data.get(key)
    if not isinstance(state, dict):
        raise ChatWebError("Conversation was not found.")
    state["title"] = title[:80]
    data[key] = state
    _write_memory_file(path, data)


def delete_conversation(config: ChatConfig, user: str, kit_dir: Path, session_id: str) -> None:
    path = memory_path_for(config, kit_dir)
    if not path or not path.exists():
        return
    data = _load_memory_file(path)
    data.pop(f"{user}:{session_id}:{kit_dir.resolve()}", None)
    _write_memory_file(path, data)


def _load_memory_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_memory_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_web_chat(config: ChatConfig, host: str = "127.0.0.1", port: int = 8765, allow_kit_switch: bool = False) -> int:
    handler = build_handler(config, allow_kit_switch=allow_kit_switch)
    server = QuietThreadingHTTPServer((host, port), handler)
    print(f"AgentBridge Web Chat control surface: http://{host}:{server.server_port}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AgentBridge Web Chat.")
    finally:
        server.server_close()
    return 0


def build_handler(base_config: ChatConfig, allow_kit_switch: bool = False) -> type[BaseHTTPRequestHandler]:
    sessions: dict[str, ChatSession] = {}

    class ChatHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_html(render_index(base_config, allow_kit_switch))
                return
            if parsed.path == "/assets/markdown-it.min.js":
                asset = files("agentbridge").joinpath("assets/markdown-it.min.js").read_bytes()
                self._send_bytes(
                    asset,
                    content_type="text/javascript; charset=utf-8",
                    cache_control="public, max-age=3600",
                )
                return
            if parsed.path == "/api/tools":
                session = self._session_from_query(parsed.query)
                self._send_json({"tools": session.tool_summaries()})
                return
            if parsed.path == "/api/conversations":
                values = parse_qs(parsed.query)
                user = values.get("user", [base_config.user])[0] or base_config.user
                kit_dir = values.get("kit", [str(base_config.kit_dir)])[0] if allow_kit_switch else str(base_config.kit_dir)
                self._send_json({"conversations": conversation_summaries(base_config, user, Path(kit_dir))})
                return
            if parsed.path == "/api/state":
                session = self._session_from_query(parsed.query)
                self._send_json(
                    {
                        "history": session.history[-session.config.max_history :],
                        "pending": session.current_pending(),
                        "tools": session.tool_summaries(),
                        "conversations": conversation_summaries(session.config, session.config.user, session.config.kit_dir),
                        "usage": dict(session.usage),
                        "runtime": runtime_payload(session),
                    }
                )
                return
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path not in {"/api/chat", "/api/chat/stream", "/api/chat/interrupt", "/api/chat/agent-permission", "/api/chat/pending", "/api/tool", "/api/connectivity", "/api/login-account", "/api/conversation"}:
                self._send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            try:
                body = self._read_json()
                web_log("request", path=parsed.path, user=body.get("user") or base_config.user, session=body.get("session_id") or base_config.session_id)
                if parsed.path == "/api/connectivity":
                    result = test_target_connectivity(
                        str(body.get("base_url", "")),
                        timeout=base_config.timeout,
                    )
                    web_log("connectivity", result=result)
                    self._send_json(result)
                    return
                session = self._session_from_body(body)
                if parsed.path == "/api/login-account":
                    self._handle_login_account(session, body)
                    return
                if parsed.path == "/api/conversation":
                    self._handle_conversation(body)
                    return
                if parsed.path == "/api/chat/interrupt":
                    web_log("chat_interrupt", session=session.config.session_id)
                    response = session.interrupt()
                    self._send_json(response.to_dict())
                    return
                if parsed.path == "/api/chat/agent-permission":
                    permission_id = str(body.get("permission_id", ""))
                    allow = bool(body.get("allow", False))
                    web_log("permission_resolve_start", session=session.config.session_id, permission_id=permission_id, allow=allow)
                    result = session.resolve_agent_permission(permission_id, allow=allow)
                    web_log("permission_resolve_done", session=session.config.session_id, permission_id=permission_id, result=result)
                    self._send_json(result)
                    return
                if parsed.path == "/api/chat/pending":
                    response = session.resolve_pending(allow=bool(body.get("allow", False)))
                    self._send_json(response.to_dict())
                    return
                if parsed.path == "/api/chat/stream":
                    message = format_chat_message_with_attachments(
                        str(body.get("message", "")),
                        body.get("attachments", []),
                    )
                    web_log("chat_stream_start", session=session.config.session_id, execute=session.config.execute)
                    self._send_sse(session.stream_process(message))
                    return
                if parsed.path == "/api/chat":
                    message = format_chat_message_with_attachments(
                        str(body.get("message", "")),
                        body.get("attachments", []),
                    )
                    response = session.process(message)
                else:
                    tool = str(body.get("tool", ""))
                    args = body.get("arguments", {})
                    if not isinstance(args, dict):
                        raise ChatWebError("arguments must be an object")
                    response = session.call_tool(tool, args, confirmed=bool(body.get("confirmed", False)))
                self._send_json(response.to_dict())
            except Exception as exc:
                web_log("request_error", path=parsed.path, error=str(exc))
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

        def _handle_login_account(self, session: ChatSession, body: dict[str, Any]) -> None:
            action = str(body.get("action") or "upsert")
            account_id = str(body.get("account_id") or "")
            if action == "delete":
                session.delete_saved_login_account(account_id)
                web_log("login_account_delete", account_id=account_id)
            elif action == "select":
                session.select_login_account(account_id)
                web_log("login_account_select", account_id=account_id)
            elif action == "upsert":
                credentials = body.get("credentials", {})
                if not isinstance(credentials, dict):
                    credentials = {}
                username = str(body.get("username") or "").strip()
                password = str(body.get("password") or "")
                if username:
                    credentials["username"] = username
                if password:
                    credentials["password"] = password
                session.upsert_saved_login_account(credentials, label=str(body.get("label") or ""), account_id=account_id)
                web_log("login_account_upsert", account_id=account_id, label=body.get("label") or username)
            else:
                raise ChatWebError("Unsupported login account action.")
            self._send_json({"runtime": runtime_payload(session)})

        def _handle_conversation(self, body: dict[str, Any]) -> None:
            user = str(body.get("user") or base_config.user)
            kit_dir = Path(str(body.get("kit_dir") or base_config.kit_dir)) if allow_kit_switch else base_config.kit_dir
            session_id = str(body.get("session_id") or body.get("session") or "")
            action = str(body.get("action") or "")
            if action == "rename":
                rename_conversation(base_config, user, kit_dir, session_id, str(body.get("title") or ""))
                web_log("conversation_rename", user=user, session=session_id)
            elif action == "delete":
                delete_conversation(base_config, user, kit_dir, session_id)
                web_log("conversation_delete", user=user, session=session_id)
            else:
                raise ChatWebError("Unsupported conversation action.")
            self._send_json({"conversations": conversation_summaries(base_config, user, kit_dir)})

        def _session_from_query(self, query: str) -> ChatSession:
            values = parse_qs(query)
            user = values.get("user", [base_config.user])[0] or base_config.user
            session_id = values.get("session", [base_config.session_id])[0] or base_config.session_id
            kit_dir = values.get("kit", [str(base_config.kit_dir)])[0] if allow_kit_switch else str(base_config.kit_dir)
            execute, base_url = self._runtime_from_values(values) if ("execute" in values or "base_url" in values) else (None, None)
            login_account_id = values.get("login_account_id", [""])[0] if "login_account_id" in values else ""
            return get_session(
                user=user,
                session_id=session_id,
                kit_dir=Path(kit_dir),
                execute=execute,
                base_url=base_url,
                login_account_id=login_account_id,
            )

        def _session_from_body(self, body: dict[str, Any]) -> ChatSession:
            user = str(body.get("user") or base_config.user)
            session_id = str(body.get("session_id") or base_config.session_id)
            kit_dir = Path(str(body.get("kit_dir") or base_config.kit_dir)) if allow_kit_switch else base_config.kit_dir
            execute, base_url = self._runtime_from_values(body)
            login_account_id = str(body.get("login_account_id") or "")
            save_login_account = body.get("save_login_account")
            return get_session(
                user=user,
                session_id=session_id,
                kit_dir=kit_dir,
                execute=execute,
                base_url=base_url,
                login_account_id=login_account_id,
                save_login_account=bool(save_login_account) if save_login_account is not None else None,
            )

        def _runtime_from_values(self, values: dict[str, Any]) -> tuple[bool, str]:
            raw_execute = values.get("execute", base_config.execute)
            if isinstance(raw_execute, list):
                raw_execute = raw_execute[0] if raw_execute else base_config.execute
            execute = raw_execute is True or str(raw_execute).lower() in {"1", "true", "yes", "on", "execute"}
            raw_base_url = values.get("base_url", base_config.base_url)
            if isinstance(raw_base_url, list):
                raw_base_url = raw_base_url[0] if raw_base_url else base_config.base_url
            base_url = str(raw_base_url or "").strip()
            if execute:
                base_url = normalize_target_base_url(base_url)
            return execute, base_url

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ChatWebError("Request body must be a JSON object")
            return data

        def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_html(self, html: str) -> None:
            self._send_bytes(html.encode("utf-8"), content_type="text/html; charset=utf-8")

        def _send_bytes(
            self,
            data: bytes,
            content_type: str,
            cache_control: str | None = None,
        ) -> None:
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", content_type)
            if cache_control:
                self.send_header("Cache-Control", cache_control)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_sse(self, events: Any) -> None:
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            for event in events:
                payload = event.to_dict() if hasattr(event, "to_dict") else dict(event)
                event_type = str(payload.get("type") or getattr(event, "type", "message"))
                if event_type in {"confirmation_required", "error", "done", "interrupted"}:
                    pending = payload.get("pending") if isinstance(payload.get("pending"), dict) else {}
                    web_log(
                        "sse_event",
                        sse_type=event_type,
                        status=payload.get("status"),
                        permission_id=pending.get("id") if isinstance(pending, dict) else "",
                        operation=pending.get("operation") if isinstance(pending, dict) else "",
                    )
                data = json.dumps(payload, sort_keys=True)
                frame = f"event: {event_type}\ndata: {data}\n\n".encode("utf-8")
                try:
                    self.wfile.write(frame)
                    flush = getattr(self.wfile, "flush", None)
                    if callable(flush):
                        flush()
                except (BrokenPipeError, ConnectionResetError):
                    break

        def _send_error(self, status: HTTPStatus, message: str) -> None:
            self._send_json({"error": message}, status=status)

        def log_message(self, format: str, *args: Any) -> None:
            return

    def get_session(
        user: str,
        session_id: str,
        kit_dir: Path,
        execute: bool | None,
        base_url: str | None,
        login_account_id: str = "",
        save_login_account: bool | None = None,
    ) -> ChatSession:
        key = f"{user}:{session_id}:{kit_dir}"
        if key not in sessions:
            sessions[key] = ChatSession(
                replace(
                    base_config,
                    user=user,
                    session_id=session_id,
                    kit_dir=kit_dir,
                    execute=execute if execute is not None else base_config.execute,
                    base_url=base_url if base_url is not None else base_config.base_url,
                    save_login_account=save_login_account if save_login_account is not None else base_config.save_login_account,
                )
            )
        session = sessions[key]
        if save_login_account is not None and session.config.save_login_account != save_login_account:
            session.config = replace(session.config, save_login_account=save_login_account)
        if execute is not None or base_url is not None:
            session.update_runtime(
                base_url=base_url if base_url is not None else session.config.base_url,
                execute=execute if execute is not None else session.config.execute,
            )
        if login_account_id:
            try:
                session.select_login_account(login_account_id)
            except ValueError:
                pass
        return session

    return ChatHandler


def memory_path_for(config: ChatConfig, kit_dir: Path) -> Path | None:
    if not config.memory_enabled:
        return None
    return config.memory_file or kit_dir / ".agentbridge-chat-memory.json"


def conversation_summaries(config: ChatConfig, user: str, kit_dir: Path, limit: int = 20) -> list[dict[str, Any]]:
    path = memory_path_for(config, kit_dir)
    if not path or not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []

    prefix = f"{user}:"
    suffix = f":{kit_dir.resolve()}"
    summaries: list[dict[str, Any]] = []
    for key, state in data.items():
        if not isinstance(key, str) or not key.startswith(prefix) or not key.endswith(suffix):
            continue
        session_id = key[len(prefix) : -len(suffix)]
        if not session_id:
            continue
        history = state.get("history", []) if isinstance(state, dict) else []
        preview = ""
        if isinstance(history, list):
            for item in reversed(history):
                if isinstance(item, dict) and item.get("content"):
                    preview = str(item["content"]).replace("\n", " ").strip()
                    break
        summaries.append(
            {
                "session_id": session_id,
                "title": str(state.get("title") or session_id)[:80] if isinstance(state, dict) else session_id,
                "preview": preview[:120],
                "message_count": len(history) if isinstance(history, list) else 0,
                "has_pending": bool(isinstance(state, dict) and state.get("pending")),
            }
        )
    return sorted(summaries, key=lambda item: item["session_id"], reverse=True)[:limit]


def format_chat_message_with_attachments(message: str, attachments: Any) -> str:
    if not isinstance(attachments, list):
        return message
    lines = ["", "Attached files:"]
    for item in attachments[:10]:
        if not isinstance(item, dict):
            continue
        summary = attachment_summary(item)
        lines.append(f"- {summary}")
        content = str(item.get("content") or "").strip()
        if content:
            lines.append("  Content:")
            for content_line in content[:8000].splitlines()[:120]:
                lines.append(f"  {content_line}")
    if len(lines) == 2:
        return message
    return message.rstrip() + "\n".join(lines)


def attachment_summaries(attachments: Any) -> list[str]:
    if not isinstance(attachments, list):
        return []
    result: list[str] = []
    for item in attachments[:10]:
        if not isinstance(item, dict):
            continue
        result.append(attachment_summary(item))
    return result


def attachment_summary(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "attachment").strip()
    size = item.get("size")
    kind = str(item.get("type") or "file").strip()
    if isinstance(size, int | float):
        return f"{name} ({kind}, {int(size)} bytes)"
    return f"{name} ({kind})"


def render_index(config: ChatConfig, allow_kit_switch: bool) -> str:
    kit = str(config.kit_dir)
    execute = "true" if config.execute else "false"
    allow_switch = "true" if allow_kit_switch else "false"
    kit_help = (
        "Choose any generated Agent Integration Kit directory for this session."
        if allow_kit_switch
        else "Start with --allow-kit-switch to edit this path."
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentBridge Chat</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #1f2522;
      --muted: #6d746f;
      --line: #e3e5e1;
      --surface: #f6f6f2;
      --surface-strong: #eeeee8;
      --panel: #ffffff;
      --accent: #16745f;
      --accent-ink: #ffffff;
      --danger: #ad4545;
      --shadow: 0 18px 45px rgba(31, 37, 34, 0.10);
    }}
    * {{ box-sizing: border-box; }}
    html {{
      height: 100%;
    }}
    body {{
      height: 100%;
      margin: 0;
      font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--surface);
      letter-spacing: 0;
      overflow: hidden;
    }}
    .app, .workspace-shell {{
      height: 100svh;
      display: grid;
      grid-template-columns: 286px minmax(0, 1fr) 320px;
      overflow: hidden;
    }}
    aside, main {{
      min-width: 0;
      min-height: 0;
    }}
    .left, .right, .sidebar-panel {{
      padding: 18px;
      border-right: 1px solid var(--line);
      background: #fbfbf8;
      overflow: auto;
    }}
    .right {{
      border-right: 0;
      border-left: 1px solid var(--line);
      display: flex;
      flex-direction: column;
    }}
    .brand {{
      font-weight: 700;
      font-size: 20px;
      margin-bottom: 4px;
    }}
    .subtle {{
      color: var(--muted);
      font-size: 13px;
    }}
    .field-help {{
      color: var(--muted);
      font-size: 12px;
      margin-top: 6px;
    }}
    label {{
      display: block;
      margin-top: 18px;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
    }}
    input, textarea, select, button {{
      font: inherit;
      letter-spacing: 0;
    }}
    input, textarea, select {{
      width: 100%;
      margin-top: 6px;
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--ink);
      padding: 10px 11px;
      border-radius: 10px;
    }}
    input:disabled {{
      color: var(--muted);
      background: #eef1ed;
    }}
    select {{
      appearance: none;
      background-image: linear-gradient(45deg, transparent 50%, #66736b 50%), linear-gradient(135deg, #66736b 50%, transparent 50%);
      background-position: calc(100% - 14px) 50%, calc(100% - 9px) 50%;
      background-size: 5px 5px, 5px 5px;
      background-repeat: no-repeat;
      padding-right: 26px;
    }}
    button {{
      border: 0;
      border-radius: 10px;
      padding: 10px 13px;
      background: var(--accent);
      color: white;
      cursor: pointer;
      transition: transform 120ms ease, opacity 120ms ease;
    }}
    button:hover {{ transform: translateY(-1px); }}
    button:disabled {{
      cursor: not-allowed;
      opacity: 0.55;
      transform: none;
    }}
    button.secondary {{
      background: #e5ebe7;
      color: var(--ink);
    }}
    button.ghost {{
      width: 100%;
      margin-top: 16px;
      background: #242925;
      color: white;
    }}
    button.icon {{
      width: 42px;
      height: 42px;
      padding: 0;
      display: inline-grid;
      place-items: center;
      background: #f0f1ed;
      color: var(--ink);
      font-size: 18px;
      line-height: 1;
    }}
    button.danger {{
      background: var(--danger);
    }}
    .main, .chat-panel {{
      display: grid;
      grid-template-rows: auto 1fr auto;
      height: 100svh;
      min-height: 0;
      background: linear-gradient(180deg, #ffffff 0%, #fafaf6 100%);
    }}
    .top, .chat-header {{
      padding: 18px 28px;
      border-bottom: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
    }}
    .mode {{
      color: {("#a43d3d" if config.execute else "#0f7b63")};
      font-weight: 650;
    }}
    .messages, .message-stream {{
      overflow: auto;
      min-height: 0;
      padding: 30px 28px 28px;
      display: grid;
      align-content: start;
      gap: 18px;
    }}
    .msg {{
      width: fit-content;
      max-width: min(72%, 720px);
      animation: rise 160ms ease-out;
    }}
    .msg.user {{
      justify-self: end;
    }}
    .msg.assistant {{
      justify-self: start;
    }}
    .role {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 6px;
      text-transform: uppercase;
      letter-spacing: 0;
    }}
    .msg.user .role {{
      text-align: right;
    }}
    .bubble {{
      white-space: pre-wrap;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px 14px;
      background: #ffffff;
      box-shadow: 0 8px 22px rgba(31, 37, 34, 0.05);
    }}
    .msg.assistant .bubble {{
      border-color: #e1e4df;
      background: #ffffff;
    }}
    .msg.user .bubble {{
      border-color: #e7e7e2;
      background: #f1f2ee;
      color: var(--ink);
    }}
    .markdown-body {{
      white-space: normal;
      overflow-wrap: anywhere;
      line-height: 1.55;
    }}
    .markdown-body p {{
      margin: 0 0 10px;
    }}
    .markdown-body p:last-child {{
      margin-bottom: 0;
    }}
    .markdown-body ul {{
      margin: 8px 0 10px;
      padding-left: 20px;
    }}
    .markdown-body li + li {{
      margin-top: 4px;
    }}
    .markdown-body strong {{
      font-weight: 700;
    }}
    .markdown-body code {{
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #f6f6f2;
      padding: 1px 5px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 0.92em;
    }}
    .composer, .composer-dock {{
      position: sticky;
      bottom: 0;
      z-index: 2;
      padding: 18px 28px 20px;
      background: linear-gradient(180deg, rgba(250,250,246,0) 0%, #fafaf6 28%, #fafaf6 100%);
    }}
    .composer-shell, .composer-card {{
      position: relative;
      border: 1px solid var(--line);
      border-radius: 22px;
      background: #fff;
      padding: 10px;
      box-shadow: var(--shadow);
      max-width: 920px;
      margin: 0 auto;
    }}
    .composer-row {{
      display: grid;
      grid-template-rows: auto auto;
      gap: 8px;
    }}
    .composer textarea {{
      min-height: 52px;
      max-height: 180px;
      resize: vertical;
      margin: 0;
      border: 0;
      padding: 8px 10px;
      outline: none;
      line-height: 1.5;
    }}
    .composer-actions {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }}
    .composer-tools {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .composer-hint {{
      color: var(--muted);
      font-size: 12px;
    }}
    .send-button {{
      width: 42px;
      height: 42px;
      padding: 0;
      border-radius: 14px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-weight: 700;
    }}
    .send-button[hidden] {{
      display: none !important;
    }}
    .send-icon {{
      width: 18px;
      height: 18px;
      display: block;
    }}
    .command-menu {{
      position: absolute;
      left: 8px;
      right: 8px;
      bottom: calc(100% + 8px);
      display: none;
      max-height: 260px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: #fff;
      box-shadow: 0 14px 34px rgba(23, 32, 27, 0.14);
      padding: 6px;
    }}
    .command-menu.show {{
      display: block;
    }}
    .suggestion {{
      width: 100%;
      border: 0;
      border-radius: 10px;
      background: transparent;
      color: var(--ink);
      text-align: left;
      padding: 9px 10px;
      display: block;
    }}
    .suggestion:hover, .conversation:hover {{
      background: #eef3f0;
      transform: none;
    }}
    .suggestion strong {{
      display: block;
      font-size: 13px;
    }}
    .attachments {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 6px;
    }}
    .attachment {{
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #f7faf8;
      color: var(--ink);
      padding: 5px 7px;
      font-size: 12px;
    }}
    .attachment button {{
      margin-left: 5px;
      padding: 0 3px;
      background: transparent;
      color: var(--muted);
    }}
    .conversation-list {{
      margin-top: 12px;
      display: grid;
      gap: 6px;
    }}
    .conversation {{
      width: 100%;
      border: 0;
      border-radius: 10px;
      background: transparent;
      color: var(--ink);
      text-align: left;
      padding: 8px 9px;
    }}
    .conversation.active {{
      background: #e5ebe7;
    }}
    .conversation strong {{
      display: block;
      font-size: 13px;
      overflow-wrap: anywhere;
    }}
    .conversation-row {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 34px;
      align-items: start;
      gap: 4px;
      border-radius: 10px;
      position: relative;
    }}
    .conversation-menu {{
      width: 32px;
      height: 32px;
      padding: 0;
      border-radius: 9px;
      background: transparent;
      color: var(--muted);
    }}
    .conversation-menu:hover {{
      background: #e8eee9;
      color: var(--ink);
      transform: none;
    }}
    .conversation-popover {{
      position: absolute;
      right: 0;
      top: 34px;
      z-index: 4;
      min-width: 132px;
      padding: 6px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fff;
      box-shadow: 0 14px 30px rgba(22,30,25,.16);
    }}
    .conversation-popover[hidden] {{
      display: none;
    }}
    .conversation-popover button {{
      width: 100%;
      display: block;
      padding: 8px 9px;
      border-radius: 8px;
      background: transparent;
      color: var(--ink);
      text-align: left;
      font-size: 13px;
    }}
    .conversation-popover button:hover {{
      background: #eef3f0;
      transform: none;
    }}
    .conversation-popover button.danger-text {{
      color: var(--danger);
    }}
    .drawer-action-row, .account-actions {{
      display: flex;
      gap: 8px;
      align-items: center;
      margin-top: 12px;
    }}
    .drawer-action-row button, .account-actions button {{
      min-height: 34px;
      padding: 7px 10px;
      border-radius: 8px;
      font-size: 12px;
    }}
    .tools {{
      margin-top: 18px;
      display: grid;
      gap: 10px;
      overflow: auto;
      min-height: 0;
      padding-right: 4px;
    }}
    .tool {{
      border-top: 1px solid var(--line);
      padding-top: 10px;
    }}
    .tool strong {{
      display: block;
      font-size: 13px;
      overflow-wrap: anywhere;
    }}
    .tool-button {{
      width: 100%;
      padding: 10px 8px;
      border-top: 1px solid var(--line);
      border-radius: 0;
      background: transparent;
      color: var(--ink);
      text-align: left;
    }}
    .tool-button:hover {{
      background: #eef3f0;
      transform: none;
    }}
    .tool-button strong {{
      display: block;
      overflow-wrap: anywhere;
      font-size: 13px;
    }}
    .tool-params {{
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      margin-top: 6px;
    }}
    .param-chip {{
      padding: 2px 5px;
      border-radius: 5px;
      background: #e9eeea;
      color: #536058;
      font-size: 11px;
    }}
    .pending {{
      margin-top: 18px;
      border-left: 3px solid var(--danger);
      padding-left: 12px;
      display: none;
    }}
    .pending.show {{
      display: block;
    }}
    .approval-card {{
      display: none;
      max-width: 820px;
      margin: 0 auto 10px;
      padding: 12px 14px;
      border: 1px solid #e2c8a8;
      border-radius: 12px;
      background: #fff9f0;
      color: #3c2e20;
      box-shadow: 0 8px 22px rgba(58, 42, 24, .08);
    }}
    .approval-card.show {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
    }}
    .approval-copy {{
      min-width: 0;
      flex: 1 1 auto;
    }}
    .approval-title {{
      font-size: 13px;
      font-weight: 700;
    }}
    .authorization-summary {{
      margin-top: 2px;
      color: var(--ink);
      font-size: 15px;
      font-weight: 700;
      overflow-wrap: anywhere;
    }}
    .authorization-subtitle {{
      margin-top: 2px;
      overflow-wrap: anywhere;
    }}
    .authorization-details {{
      margin-top: 8px;
      max-width: 100%;
    }}
    .authorization-details summary {{
      cursor: pointer;
      color: #6a4c2a;
      font-size: 12px;
      font-weight: 700;
    }}
    .authorization-command {{
      max-width: 100%;
      max-height: 120px;
      overflow: auto;
      margin: 6px 0 0;
      padding: 8px;
      border-radius: 8px;
      background: rgba(255, 255, 255, .72);
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      word-break: break-word;
      font-size: 12px;
      line-height: 1.45;
    }}
    .approval-actions {{
      display: flex;
      flex: 0 0 auto;
      gap: 8px;
    }}
    .usage-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 14px;
    }}
    .usage-stat {{
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }}
    .usage-stat strong {{
      display: block;
      margin-top: 3px;
      font-size: 18px;
      font-variant-numeric: tabular-nums;
    }}
    .timeline {{
      display: grid;
      gap: 6px;
      margin-top: 14px;
    }}
    .timeline-item {{
      padding: 8px 9px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }}
    .usage-history {{
      display: grid;
      gap: 6px;
      margin-top: 14px;
    }}
    .usage-history-item {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      padding: 8px 9px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--muted);
      font-size: 12px;
      font-variant-numeric: tabular-nums;
    }}
    .account-manager {{
      margin-top: 18px;
      padding-top: 14px;
      border-top: 1px solid var(--line);
    }}
    .drawer-pane[data-pane="accounts"] .account-manager {{
      margin-top: 0;
      padding-top: 0;
      border-top: 0;
    }}
    .account-toolbar {{
      margin-top: 14px;
    }}
    .account-toolbar button {{
      min-height: 34px;
      padding: 7px 10px;
      border-radius: 8px;
      font-size: 12px;
    }}
    .account-list {{
      margin-top: 12px;
      display: grid;
      gap: 6px;
    }}
    .account-row {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 34px;
      align-items: start;
      gap: 4px;
      border-radius: 10px;
      position: relative;
    }}
    .account-card {{
      width: 100%;
      border: 0;
      border-radius: 10px;
      background: transparent;
      color: var(--ink);
      text-align: left;
      padding: 8px 9px;
    }}
    .account-card:hover, .account-card.active {{
      background: #e5ebe7;
      transform: none;
    }}
    .account-card strong {{
      display: block;
      font-size: 13px;
      overflow-wrap: anywhere;
    }}
    .account-card .subtle {{
      display: block;
      margin-top: 1px;
      overflow-wrap: anywhere;
    }}
    .account-menu {{
      width: 32px;
      height: 32px;
      padding: 0;
      border-radius: 9px;
      background: transparent;
      color: var(--muted);
    }}
    .account-menu:hover {{
      background: #e8eee9;
      color: var(--ink);
      transform: none;
    }}
    .account-popover {{
      position: absolute;
      right: 0;
      top: 34px;
      z-index: 4;
      min-width: 132px;
      padding: 6px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fff;
      box-shadow: 0 14px 30px rgba(22,30,25,.16);
    }}
    .account-popover[hidden] {{
      display: none;
    }}
    .account-popover button {{
      width: 100%;
      display: block;
      padding: 8px 9px;
      border-radius: 8px;
      background: transparent;
      color: var(--ink);
      text-align: left;
      font-size: 13px;
    }}
    .account-popover button:hover {{
      background: #eef3f0;
      transform: none;
    }}
    .account-popover button.danger-text {{
      color: var(--danger);
    }}
    .account-editor {{
      margin-top: 14px;
      padding-top: 14px;
      border-top: 1px solid var(--line);
    }}
    .account-editor[hidden] {{
      display: none;
    }}
    .command-run-group {{
      margin-top: 8px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfbf8;
      padding: 0;
    }}
    .command-run-group summary {{
      cursor: pointer;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      list-style: none;
      padding: 7px 8px;
    }}
    .command-run-group summary::-webkit-details-marker {{
      display: none;
    }}
    .command-run-count {{
      display: inline-flex;
      align-items: center;
      gap: 7px;
    }}
    .command-run-count::before {{
      content: "▸";
      color: #8b928d;
      font-size: 11px;
    }}
    .command-run-group[open] .command-run-count::before {{
      content: "▾";
    }}
    .command-run-list {{
      display: grid;
      gap: 8px;
      padding: 0 8px 8px;
    }}
    .command-run-item {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 8px;
    }}
    .command-run-title {{
      margin-bottom: 6px;
      color: var(--ink);
      font-size: 12px;
      font-weight: 650;
      overflow-wrap: anywhere;
    }}
    .command-run-item pre {{
      max-height: 160px;
      overflow: auto;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      margin: 0;
      font-size: 12px;
    }}
    .actions {{
      display: flex;
      gap: 8px;
      margin-top: 10px;
    }}
    .app, .workspace-shell {{
      grid-template-columns: 64px minmax(0, 1fr) auto;
      background: var(--panel);
    }}
    .navigation-rail {{
      z-index: 5;
      padding: 10px;
      border-right: 1px solid var(--line);
      background: #f7f8f5;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 8px;
    }}
    .brand-mark {{
      width: 42px;
      height: 42px;
      margin-bottom: 10px;
      border-radius: 12px;
      display: grid;
      place-items: center;
      background: #1f2924;
      color: #fff;
      font-weight: 750;
      font-size: 17px;
    }}
    .rail-spacer {{
      flex: 1;
    }}
    .rail-button, .mobile-menu, .drawer-close {{
      width: 44px;
      height: 44px;
      padding: 0;
      border-radius: 12px;
      display: grid;
      place-items: center;
      background: transparent;
      color: #536058;
    }}
    .rail-button:hover, .rail-button.active, .mobile-menu:hover, .drawer-close:hover {{
      background: #e6ebe7;
      color: #17201b;
      transform: none;
    }}
    .rail-button svg, .mobile-menu svg, .drawer-close svg {{
      width: 20px;
      height: 20px;
      fill: none;
      stroke: currentColor;
      stroke-width: 1.8;
      stroke-linecap: round;
      stroke-linejoin: round;
    }}
    .main, .chat-panel {{
      min-width: 0;
      background: #fff;
    }}
    .top, .chat-header {{
      min-height: 64px;
      padding: 10px 22px;
      background: rgba(255,255,255,.94);
      backdrop-filter: blur(12px);
    }}
    .header-leading {{
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }}
    .header-copy {{
      min-width: 0;
    }}
    .header-title {{
      display: block;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 15px;
    }}
    .header-meta {{
      margin-top: 1px;
      color: var(--muted);
      font-size: 12px;
    }}
    .runtime-controls {{
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 10px;
      min-width: 0;
    }}
    .runtime-mode {{
      display: inline-flex;
      padding: 3px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #f1f3f0;
    }}
    .runtime-mode button {{
      min-height: 34px;
      padding: 6px 10px;
      border-radius: 7px;
      background: transparent;
      color: #59645d;
      font-size: 12px;
      font-weight: 650;
    }}
    .runtime-mode button:hover {{
      transform: none;
      color: var(--ink);
    }}
    .runtime-mode button.active {{
      background: #fff;
      color: var(--ink);
      box-shadow: 0 1px 4px rgba(30, 38, 33, .12);
    }}
    .runtime-target {{
      display: flex;
      align-items: center;
      gap: 7px;
      min-width: 0;
      padding: 4px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #f7f8f5;
    }}
    .runtime-target[hidden] {{
      display: none;
    }}
    .runtime-target input {{
      width: min(32vw, 310px);
      min-width: 180px;
      height: 36px;
      margin: 0;
      padding: 7px 9px;
      border-radius: 8px;
      font-size: 13px;
      background: #fff;
    }}
    .login-account-select {{
      width: min(20vw, 180px);
      min-width: 138px;
      height: 36px;
      margin: 0;
      padding: 7px 26px 7px 9px;
      border-radius: 8px;
      font-size: 13px;
    }}
    .connection-button {{
      min-width: 86px;
      height: 36px;
      padding: 7px 10px;
      border-radius: 8px;
      background: #e5ebe7;
      color: var(--ink);
      font-size: 12px;
      font-weight: 650;
    }}
    .connection-button:hover {{
      transform: none;
      background: #dbe4de;
    }}
    .connection-status {{
      width: 24px;
      height: 24px;
      display: inline-grid;
      place-items: center;
      flex: 0 0 24px;
      color: var(--muted);
      font-size: 17px;
      font-weight: 800;
      overflow: hidden;
      line-height: 1;
    }}
    .connection-status.success {{
      color: #08705a;
    }}
    .connection-status.error {{
      color: var(--danger);
    }}
    .mobile-menu {{
      display: none;
    }}
    .mode {{
      padding: 5px 9px;
      border-radius: 999px;
      background: {("#fff0f0" if config.execute else "#e8f3ef")};
      font-size: 12px;
    }}
    .messages, .message-stream {{
      padding: 34px clamp(20px, 6vw, 88px) 40px;
      gap: 28px;
      grid-template-columns: minmax(0, 1fr);
      scroll-behavior: smooth;
    }}
    .reading-column::before {{
      content: "";
      width: min(100%, 820px);
      justify-self: center;
    }}
    .msg {{
      min-width: 0;
      width: 100%;
      max-width: 820px;
      justify-self: center;
    }}
    .msg.user {{
      width: fit-content;
      max-width: min(72%, 680px);
      justify-self: end;
      margin-right: max(0px, calc((100% - 820px) / 2));
    }}
    .msg.assistant {{
      justify-self: center;
    }}
    .role {{
      display: flex;
      align-items: center;
      gap: 7px;
      margin-bottom: 8px;
      font-size: 11px;
      font-weight: 650;
    }}
    .msg.assistant .role::before {{
      content: "A";
      width: 22px;
      height: 22px;
      border-radius: 7px;
      display: grid;
      place-items: center;
      background: #1f2924;
      color: #fff;
      font-size: 10px;
      font-weight: 750;
    }}
    .msg.user .role {{
      display: none;
    }}
    .bubble {{
      box-shadow: none;
    }}
    .msg.assistant .bubble {{
      padding: 0 0 0 29px;
      border: 0;
      border-radius: 0;
      background: transparent;
    }}
    .msg.user .bubble {{
      padding: 10px 14px;
      border: 1px solid #e1e5e1;
      border-radius: 16px 16px 4px 16px;
      background: #f0f2ef;
    }}
    .markdown-body {{
      min-width: 0;
      color: #242a26;
      font-size: 15px;
      line-height: 1.72;
    }}
    .markdown-body h1, .markdown-body h2, .markdown-body h3,
    .markdown-body h4, .markdown-body h5, .markdown-body h6 {{
      margin: 1.4em 0 .55em;
      color: #17201b;
      line-height: 1.3;
      font-weight: 700;
    }}
    .markdown-body h1:first-child, .markdown-body h2:first-child,
    .markdown-body h3:first-child {{
      margin-top: 0;
    }}
    .markdown-body h1 {{ font-size: 1.55rem; }}
    .markdown-body h2 {{ font-size: 1.3rem; }}
    .markdown-body h3 {{ font-size: 1.12rem; }}
    .markdown-body p {{
      margin: 0 0 12px;
    }}
    .markdown-body ul, .markdown-body ol {{
      margin: 8px 0 16px;
      padding-left: 24px;
    }}
    .markdown-body li + li {{
      margin-top: 5px;
    }}
    .markdown-body blockquote {{
      margin: 14px 0;
      padding: 2px 0 2px 14px;
      border-left: 3px solid #b8c5be;
      color: #56615b;
    }}
    .markdown-body pre {{
      margin: 14px 0;
      padding: 14px 16px;
      overflow: auto;
      border: 1px solid #dfe4e0;
      border-radius: 10px;
      background: #f5f7f5;
      line-height: 1.55;
    }}
    .markdown-body pre code {{
      padding: 0;
      border: 0;
      background: transparent;
      font-size: 13px;
    }}
    .markdown-body a {{
      color: #096c58;
      text-decoration-thickness: 1px;
      text-underline-offset: 2px;
    }}
    .markdown-body hr {{
      margin: 22px 0;
      border: 0;
      border-top: 1px solid var(--line);
    }}
    .table-scroll {{
      width: 100%;
      max-width: 100%;
      margin: 14px 0 18px;
      overflow-x: auto;
      border: 1px solid #dfe4e0;
      border-radius: 10px;
    }}
    .markdown-body table {{
      width: 100%;
      min-width: 520px;
      border-collapse: collapse;
      font-size: 14px;
    }}
    .markdown-body th, .markdown-body td {{
      padding: 9px 12px;
      border-right: 1px solid #e4e8e5;
      border-bottom: 1px solid #e4e8e5;
      text-align: left;
      vertical-align: top;
    }}
    .markdown-body th {{
      background: #f3f6f3;
      color: #26302a;
      font-weight: 650;
    }}
    .markdown-body tr:last-child td {{
      border-bottom: 0;
    }}
    .markdown-body th:last-child, .markdown-body td:last-child {{
      border-right: 0;
    }}
    .plain-text {{
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }}
    .composer, .composer-dock {{
      padding: 12px clamp(20px, 6vw, 88px) 18px;
      background: linear-gradient(180deg, rgba(255,255,255,0) 0%, #fff 26%, #fff 100%);
    }}
    .composer-shell, .composer-card {{
      max-width: 820px;
      border-radius: 18px;
      padding: 9px;
      box-shadow: 0 14px 38px rgba(31, 42, 36, .12);
    }}
    .composer textarea {{
      min-height: 48px;
      padding: 8px 10px 2px;
      resize: none;
      font-size: 16px;
    }}
    .composer textarea:focus-visible {{
      outline: none;
    }}
    .composer-card:focus-within {{
      border-color: #9eb5aa;
      box-shadow: 0 0 0 3px rgba(22,116,95,.11), 0 14px 38px rgba(31,42,36,.12);
    }}
    .send-button {{
      width: 40px;
      height: 40px;
      border-radius: 12px;
    }}
    .context-drawer, .drawer-panel {{
      width: 320px;
      height: 100svh;
      border-left: 1px solid var(--line);
      background: #fafbf9;
      display: none;
      grid-template-rows: auto 1fr;
      overflow: hidden;
    }}
    .context-drawer.open {{
      display: grid;
    }}
    .drawer-header {{
      min-height: 64px;
      padding: 10px 12px 10px 18px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .drawer-title {{
      font-weight: 700;
    }}
    .drawer-content {{
      padding: 18px;
      overflow: auto;
    }}
    .drawer-pane {{
      display: none;
    }}
    .drawer-pane.active {{
      display: block;
    }}
    .drawer-pane label:first-child {{
      margin-top: 0;
    }}
    .drawer-backdrop {{
      display: none;
    }}
    button:focus-visible, input:focus-visible, textarea:focus-visible {{
      outline: 3px solid rgba(22,116,95,.25);
      outline-offset: 2px;
    }}
    @keyframes rise {{
      from {{ opacity: 0; transform: translateY(4px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    @media (max-width: 760px) {{
      .app, .workspace-shell {{
        grid-template-columns: minmax(0, 1fr);
      }}
      .navigation-rail {{
        display: none;
      }}
      .mobile-menu {{
        display: grid;
      }}
      .top, .chat-header {{
        padding: 9px 12px;
        align-items: flex-start;
        flex-wrap: wrap;
      }}
      .header-meta {{
        display: none;
      }}
      .runtime-controls {{
        width: 100%;
        justify-content: flex-start;
        flex-wrap: wrap;
        padding-left: 54px;
      }}
      .runtime-target {{
        width: 100%;
        flex-wrap: wrap;
      }}
      .runtime-target input {{
        width: 100%;
        min-width: 0;
        flex: 1;
      }}
      .connection-status {{
        width: 24px;
        max-width: 24px;
        padding-left: 2px;
      }}
      .messages, .message-stream {{
        padding: 24px 16px 30px;
      }}
      .msg.user {{
        max-width: 88%;
        margin-right: 0;
      }}
      .msg.assistant .bubble {{
        padding-left: 0;
      }}
      .composer, .composer-dock {{
        padding: 10px 12px max(12px, env(safe-area-inset-bottom));
      }}
      .approval-card.show {{
        align-items: stretch;
        flex-direction: column;
      }}
      .approval-actions button {{
        flex: 1;
      }}
      .composer-hint {{
        display: none;
      }}
      .context-drawer, .drawer-panel {{
        position: fixed;
        inset: 0 auto 0 0;
        z-index: 20;
        width: min(88vw, 340px);
        border-left: 0;
        border-right: 1px solid var(--line);
        box-shadow: 18px 0 42px rgba(22,30,25,.18);
      }}
      .drawer-backdrop.show {{
        position: fixed;
        inset: 0;
        z-index: 19;
        display: block;
        border: 0;
        border-radius: 0;
        background: rgba(22,28,24,.42);
      }}
      .markdown-body {{
        font-size: 16px;
      }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      *, *::before, *::after {{
        scroll-behavior: auto !important;
        animation-duration: .01ms !important;
        transition-duration: .01ms !important;
      }}
    }}
  </style>
</head>
<body>
  <div class="app workspace-shell">
    <nav class="navigation-rail" aria-label="Workspace navigation">
      <div class="brand-mark" title="AgentBridge">A</div>
      <button class="rail-button" type="button" data-drawer="conversations" title="Recent conversations" aria-label="Recent conversations">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a8.5 8.5 0 0 1-9 8.5 9.5 9.5 0 0 1-4-.9L3 21l1.4-4A8.5 8.5 0 1 1 21 12Z"></path></svg>
      </button>
      <button class="rail-button" type="button" data-drawer="context" title="Chat context" aria-label="Chat context">
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="3"></circle><path d="M5.5 20a6.5 6.5 0 0 1 13 0"></path></svg>
      </button>
      <button class="rail-button" type="button" data-drawer="tools" title="Available tools" aria-label="Available tools">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14.7 6.3 3-3a4.2 4.2 0 0 1-5.4 5.4l-7.6 7.6a2.1 2.1 0 0 0 3 3l7.6-7.6a4.2 4.2 0 0 0 5.4-5.4l-3 3"></path></svg>
      </button>
      <button class="rail-button" id="usageButton" type="button" data-drawer="usage" title="AI usage" aria-label="AI usage">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 19V9m7 10V5m7 14v-7"></path></svg>
      </button>
      <div class="rail-spacer"></div>
      <button class="rail-button" id="newChatBtn" type="button" title="New chat" aria-label="New chat">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"></path></svg>
      </button>
    </nav>
    <main class="main chat-panel">
      <div class="top chat-header">
        <div class="header-leading">
          <button class="mobile-menu" id="mobileMenuBtn" type="button" aria-label="Open navigation">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"></path></svg>
          </button>
          <div class="header-copy">
            <strong class="header-title">AgentBridge</strong>
            <div class="header-meta">Control parsed system capabilities through Claude Agent chat</div>
          </div>
        </div>
        <div class="runtime-controls">
          <div class="runtime-mode" id="runtimeMode" role="group" aria-label="Runtime mode">
            <button type="button" data-mode="dry-run" aria-pressed="false">Dry-run</button>
            <button type="button" data-mode="execute" aria-pressed="false">Real system</button>
          </div>
          <div class="runtime-target" id="runtimeTarget" hidden>
            <input
              id="baseUrl"
              type="url"
              value="{escape_attr(config.base_url)}"
              placeholder="http://localhost:8080"
              aria-label="Target system base URL"
            >
            <select class="login-account-select" id="loginAccount" aria-label="Saved login account">
              <option value="">No saved account</option>
            </select>
            <input id="saveLoginAccount" type="checkbox" checked hidden>
            <button class="connection-button" id="manageAccountsBtn" type="button">Accounts</button>
            <button class="connection-button" id="testConnectionBtn" type="button">Test connection</button>
            <span class="connection-status" id="connectionStatus" aria-live="polite"></span>
          </div>
        </div>
      </div>
      <div class="messages message-stream reading-column" id="messages" aria-live="polite"></div>
      <div class="composer composer-dock">
        <div class="approval-card" id="pending">
          <div class="approval-copy">
            <div class="approval-title">Authorization required</div>
            <div class="authorization-summary" id="pendingSummary"></div>
            <div class="subtle authorization-subtitle" id="pendingText"></div>
            <details class="authorization-details" id="pendingDetails">
              <summary>Command details</summary>
              <pre class="authorization-command" id="pendingCommand"></pre>
            </details>
          </div>
          <div class="approval-actions">
            <button id="confirmBtn" type="button">Authorize</button>
            <button class="secondary" id="cancelBtn" type="button">Cancel</button>
          </div>
        </div>
        <div class="composer-shell composer-card">
          <div class="command-menu" id="commandMenu">
            <button class="suggestion" data-command="/tools"><strong>/tools</strong><span class="subtle">List tools in the parsed system layer</span></button>
            <button class="suggestion" data-command="/run"><strong>/run</strong><span class="subtle">Run a generated system tool with key=value arguments</span></button>
            <button class="suggestion" data-command="confirm"><strong>confirm</strong><span class="subtle">Approve the pending high-risk operation</span></button>
            <button class="suggestion" data-command="cancel"><strong>cancel</strong><span class="subtle">Clear the pending operation</span></button>
          </div>
          <div class="attachments" id="attachments"></div>
          <div class="composer-row">
            <input id="fileInput" type="file" multiple hidden>
            <textarea id="message" placeholder="Ask Claude to inspect, dry-run, or operate the parsed system..."></textarea>
            <div class="composer-actions">
              <div class="composer-tools">
                <button class="icon" id="attachBtn" title="Attach files" aria-label="Attach files" type="button">
                  <svg viewBox="0 0 24 24" width="19" height="19" aria-hidden="true"><path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path></svg>
                </button>
                <span class="composer-hint">Enter to send, Shift+Enter for newline</span>
              </div>
              <button class="send-button" id="send" title="Send message" aria-label="Send message">
                <svg class="send-icon" viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M12 19V5m0 0-6 6m6-6 6 6" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"></path>
                </svg>
              </button>
              <button class="send-button danger" id="interruptBtn" title="Stop current request" aria-label="Stop current request" hidden>
                <svg class="send-icon" viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M7 7h10v10H7z" fill="currentColor"></path>
                </svg>
              </button>
            </div>
          </div>
        </div>
      </div>
    </main>
    <aside class="context-drawer drawer-panel" id="contextDrawer" aria-label="Workspace details">
      <div class="drawer-header">
        <div class="drawer-title" id="drawerTitle">Recent conversations</div>
        <button class="drawer-close" id="drawerCloseBtn" type="button" title="Close panel" aria-label="Close panel">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"></path></svg>
        </button>
      </div>
      <div class="drawer-content">
        <section class="drawer-pane active" data-pane="conversations">
          <div class="subtle">Continue a previous session or start a new chat.</div>
          <div class="drawer-action-row">
            <button id="drawerNewChatBtn" type="button">New chat</button>
          </div>
          <div class="conversation-list" id="conversations"></div>
        </section>
        <section class="drawer-pane" data-pane="context">
          <label for="user">User</label>
          <input id="user" value="{escape_attr(config.user)}">
          <div class="field-help">Operator identity used for memory and audit context.</div>
          <label for="session">Session</label>
          <input id="session" value="{escape_attr(config.session_id)}">
          <div class="field-help">Session memory is grouped by user and session.</div>
          <label for="kit">Kit</label>
          <input id="kit" value="{escape_attr(kit)}" {"disabled" if not allow_kit_switch else ""}>
          <div class="field-help">{kit_help}</div>
        </section>
        <section class="drawer-pane" data-pane="accounts">
          <div class="account-manager" id="accountManager">
            <strong>Saved accounts</strong>
            <div class="field-help">Choose a saved login, edit it, delete it, or add another account.</div>
            <div class="account-toolbar">
              <button id="newAccountFormBtn" type="button">New account</button>
            </div>
            <div class="account-list" id="accountList"></div>
            <form class="account-editor" id="accountForm" hidden>
              <strong id="accountEditor">New account</strong>
              <label for="accountLabel">Label</label>
              <input id="accountLabel" autocomplete="off" placeholder="Admin">
              <label for="accountUsername">Username</label>
              <input id="accountUsername" autocomplete="username" placeholder="admin">
              <label for="accountPassword">Password</label>
              <input id="accountPassword" type="password" autocomplete="current-password" placeholder="Leave blank to keep current password">
              <div class="account-actions">
                <button id="saveAccountBtn" type="submit">Save account</button>
                <button class="secondary" id="cancelAccountEditBtn" type="button">Cancel</button>
              </div>
            </form>
          </div>
        </section>
        <section class="drawer-pane" data-pane="tools">
          <div class="subtle">Select a tool to insert a runnable command and its required parameters.</div>
          <div class="tools" id="tools"></div>
        </section>
        <section class="drawer-pane" data-pane="usage" id="usagePanel">
          <div class="subtle">Claude Agent SDK token usage for the current session.</div>
          <div class="usage-grid">
            <div class="usage-stat"><span class="subtle">Last input</span><strong id="usageInput">0</strong></div>
            <div class="usage-stat"><span class="subtle">Last output</span><strong id="usageOutput">0</strong></div>
            <div class="usage-stat"><span class="subtle">Last total</span><strong id="usageTotal">0</strong></div>
            <div class="usage-stat"><span class="subtle">Session total</span><strong id="usageSessionTotal">0</strong></div>
          </div>
          <div class="usage-history" id="usageHistory"></div>
        </section>
      </div>
    </aside>
    <button class="drawer-backdrop" id="drawerBackdrop" type="button" aria-label="Close navigation"></button>
  </div>
  <script src="/assets/markdown-it.min.js"></script>
  <script>
    const allowKitSwitch = {allow_switch};
    const initialExecuteMode = {execute};
    const els = {{
      user: document.getElementById('user'),
      session: document.getElementById('session'),
      kit: document.getElementById('kit'),
      messages: document.getElementById('messages'),
      message: document.getElementById('message'),
      send: document.getElementById('send'),
      interrupt: document.getElementById('interruptBtn'),
      fileInput: document.getElementById('fileInput'),
      attachments: document.getElementById('attachments'),
      commandMenu: document.getElementById('commandMenu'),
      conversations: document.getElementById('conversations'),
      tools: document.getElementById('tools'),
      pending: document.getElementById('pending'),
      pendingSummary: document.getElementById('pendingSummary'),
      pendingText: document.getElementById('pendingText'),
      pendingDetails: document.getElementById('pendingDetails'),
      pendingCommand: document.getElementById('pendingCommand'),
      confirm: document.getElementById('confirmBtn'),
      cancel: document.getElementById('cancelBtn'),
      contextDrawer: document.getElementById('contextDrawer'),
      drawerTitle: document.getElementById('drawerTitle'),
      drawerBackdrop: document.getElementById('drawerBackdrop'),
      runtimeMode: document.getElementById('runtimeMode'),
      runtimeTarget: document.getElementById('runtimeTarget'),
      baseUrl: document.getElementById('baseUrl'),
      loginAccount: document.getElementById('loginAccount'),
      saveLoginAccount: document.getElementById('saveLoginAccount'),
      manageAccounts: document.getElementById('manageAccountsBtn'),
      accountList: document.getElementById('accountList'),
      accountForm: document.getElementById('accountForm'),
      accountEditor: document.getElementById('accountEditor'),
      accountLabel: document.getElementById('accountLabel'),
      accountUsername: document.getElementById('accountUsername'),
      accountPassword: document.getElementById('accountPassword'),
      saveAccount: document.getElementById('saveAccountBtn'),
      newAccountForm: document.getElementById('newAccountFormBtn'),
      cancelAccountEdit: document.getElementById('cancelAccountEditBtn'),
      drawerNewChat: document.getElementById('drawerNewChatBtn'),
      testConnection: document.getElementById('testConnectionBtn'),
      connectionStatus: document.getElementById('connectionStatus'),
      usageInput: document.getElementById('usageInput'),
      usageOutput: document.getElementById('usageOutput'),
      usageTotal: document.getElementById('usageTotal'),
      usageSessionTotal: document.getElementById('usageSessionTotal'),
      usageHistory: document.getElementById('usageHistory')
    }};
    let toolsCache = [];
    let loginAccountsCache = [];
    let attachments = [];
    let sendInFlight = false;
    let awaitingAuthorization = false;
    let activeStreamController = null;
    let visibleIdleTimer = null;
    let runtimeExecute = initialExecuteMode;
    let currentPending = null;
    let editingAccountId = '';
    let commandSummaryNode = null;
    const renderedCommandKeys = new Set();
    const commandDetailsByNode = new WeakMap();
    const STREAM_VISIBLE_IDLE_TIMEOUT_MS = 20000;
    const markdownRenderer = window.markdownit ? window.markdownit({{
      html: false,
      linkify: true,
      typographer: false,
      breaks: false
    }}) : null;
    const allowedMarkdownTags = new Set([
      'A', 'BLOCKQUOTE', 'BR', 'CODE', 'DEL', 'EM', 'H1', 'H2', 'H3',
      'H4', 'H5', 'H6', 'HR', 'LI', 'OL', 'P', 'PRE', 'S', 'STRONG',
      'TABLE', 'TBODY', 'TD', 'TH', 'THEAD', 'TR', 'UL'
    ]);
    const drawerTitles = {{
      conversations: 'Recent conversations',
      context: 'Chat context',
      accounts: 'Saved accounts',
      tools: 'Available tools',
      usage: 'AI usage'
    }};
    function payload(extra = {{}}) {{
      return Object.assign({{
        user: els.user.value,
        session_id: els.session.value,
        kit_dir: allowKitSwitch ? els.kit.value : undefined,
        execute: runtimeExecute,
        base_url: runtimeExecute ? els.baseUrl.value.trim() : '',
        login_account_id: selectedLoginAccountId(),
        save_login_account: els.saveLoginAccount.checked
      }}, extra);
    }}
    function selectedLoginAccountId() {{
      return els.loginAccount && !els.loginAccount.disabled ? els.loginAccount.value : '';
    }}
    function stateQuery(includeRuntime = false) {{
      const qs = new URLSearchParams({{ user: els.user.value, session: els.session.value }});
      if (allowKitSwitch) qs.set('kit', els.kit.value);
      if (includeRuntime) {{
        qs.set('execute', runtimeExecute ? 'true' : 'false');
        if (runtimeExecute) qs.set('base_url', els.baseUrl.value.trim());
        if (selectedLoginAccountId()) qs.set('login_account_id', selectedLoginAccountId());
      }}
      return qs;
    }}
    function setConnectionStatus(message = '', state = '') {{
      els.connectionStatus.textContent = state === 'success' ? '✓' : (state === 'error' ? '×' : '');
      els.connectionStatus.className = 'connection-status' + (state ? ' ' + state : '');
      els.connectionStatus.title = message;
      els.connectionStatus.setAttribute('aria-label', message || 'Connection status');
    }}
    function validRuntimeBaseUrl(showError = true) {{
      if (!runtimeExecute) return true;
      const value = els.baseUrl.value.trim();
      try {{
        const parsed = new URL(value);
        if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.host) throw new Error();
        return true;
      }} catch (error) {{
        if (showError) setConnectionStatus('Enter a valid http(s) Base URL.', 'error');
        return false;
      }}
    }}
    function applyRuntimeMode(mode, reload = true) {{
      runtimeExecute = mode === 'execute';
      els.runtimeMode.querySelectorAll('[data-mode]').forEach(button => {{
        const active = button.dataset.mode === mode;
        button.classList.toggle('active', active);
        button.setAttribute('aria-pressed', active ? 'true' : 'false');
      }});
      els.runtimeTarget.hidden = !runtimeExecute;
      renderPending(null);
      setConnectionStatus();
      if (reload && validRuntimeBaseUrl(false)) loadState(true);
      if (runtimeExecute && !els.baseUrl.value.trim()) {{
        setConnectionStatus('Base URL is required for real system mode.', 'error');
        els.baseUrl.focus();
      }}
    }}
    async function testConnectivity() {{
      if (!validRuntimeBaseUrl(true)) return;
      els.testConnection.disabled = true;
      setConnectionStatus();
      try {{
        const data = await post('/api/connectivity', {{ base_url: els.baseUrl.value.trim() }});
        if (data.error) {{
          setConnectionStatus(data.error, 'error');
        }} else if (data.reachable) {{
          setConnectionStatus('Reachable · HTTP ' + data.status + ' · ' + data.elapsed_ms + ' ms', 'success');
        }} else {{
          setConnectionStatus('Unreachable · ' + (data.error || 'Connection failed'), 'error');
        }}
      }} catch (error) {{
        setConnectionStatus('Connection test failed.', 'error');
      }} finally {{
        els.testConnection.disabled = false;
      }}
    }}
    function isSafeLink(value) {{
      const href = String(value || '').trim();
      if (!href) return false;
      const scheme = href.match(/^([a-z][a-z0-9+.-]*):/i);
      return !scheme || ['http', 'https', 'mailto', 'tel'].includes(scheme[1].toLowerCase());
    }}
    function sanitizeMarkdownFragment(fragment) {{
      Array.from(fragment.querySelectorAll('*')).forEach(element => {{
        if (!allowedMarkdownTags.has(element.tagName)) {{
          element.replaceWith(document.createTextNode(element.textContent || ''));
          return;
        }}
        Array.from(element.attributes).forEach(attribute => {{
          const name = attribute.name.toLowerCase();
          const isLinkAttribute = element.tagName === 'A' && ['href', 'title'].includes(name);
          const isCodeClass = element.tagName === 'CODE' && name === 'class' && /^language-[a-z0-9_-]+$/i.test(attribute.value);
          const isTableAlignment = ['TH', 'TD'].includes(element.tagName) &&
            name === 'style' && /^text-align:\\s*(left|right|center);?$/i.test(attribute.value);
          if (!isLinkAttribute && !isCodeClass && !isTableAlignment) {{
            element.removeAttribute(attribute.name);
          }}
        }});
        if (element.tagName === 'A') {{
          if (!isSafeLink(element.getAttribute('href'))) {{
            element.removeAttribute('href');
          }} else {{
            element.setAttribute('target', '_blank');
            element.setAttribute('rel', 'noopener noreferrer');
          }}
        }}
      }});
      Array.from(fragment.querySelectorAll('table')).forEach(table => {{
        const wrapper = document.createElement('div');
        wrapper.className = 'table-scroll';
        table.replaceWith(wrapper);
        wrapper.appendChild(table);
      }});
      return fragment;
    }}
    function renderMarkdown(text) {{
      const template = document.createElement('template');
      if (!markdownRenderer) {{
        const fallback = document.createElement('p');
        fallback.className = 'plain-text';
        fallback.textContent = String(text || '');
        template.content.appendChild(fallback);
        return template.content;
      }}
      template.innerHTML = markdownRenderer.render(String(text || ''));
      return sanitizeMarkdownFragment(template.content);
    }}
    function renderPlainText(text) {{
      const fragment = document.createDocumentFragment();
      const content = document.createElement('div');
      content.className = 'plain-text';
      content.textContent = String(text || '');
      fragment.appendChild(content);
      return fragment;
    }}
    function addMessage(role, text) {{
      const node = document.createElement('div');
      node.className = 'msg ' + role;
      node.innerHTML = '<div class="role"></div><div class="bubble markdown-body"></div>';
      node.querySelector('.role').textContent = role === 'assistant' ? 'AgentBridge' : role;
      const bubble = node.querySelector('.bubble');
      bubble.replaceChildren(role === 'user' ? renderPlainText(text) : renderMarkdown(text));
      els.messages.appendChild(node);
      els.messages.scrollTop = els.messages.scrollHeight;
      return node;
    }}
    function updateAssistantMessage(node, text) {{
      const bubble = node.querySelector('.bubble');
      bubble.replaceChildren(renderMarkdown(text));
      renderCommandDetails(node);
      els.messages.scrollTop = els.messages.scrollHeight;
    }}
    function humanizeIdentifier(value) {{
      const text = String(value || '').replace(/[_-]+/g, ' ').trim();
      return text ? text.charAt(0).toUpperCase() + text.slice(1) : 'Agent operation';
    }}
    function pendingOperation(pending) {{
      if (!pending) return 'Agent operation';
      if (pending.operation) return pending.operation;
      if (pending.kind === 'agent_permission') {{
        return summarizeCommand((pending.input || {{}}).command || '') || humanizeIdentifier(pending.display_name || pending.tool || pending.title);
      }}
      return humanizeIdentifier(pending.display_name || pending.name || pending.tool || 'System operation');
    }}
    function summarizeCommand(command) {{
      const text = String(command || '');
      const lowered = text.toLowerCase();
      if (lowered.includes('/auth/login') || lowered.includes('/login')) return 'Login';
      const methodMatch = lowered.match(/(?:-x|--request)\\s+([a-z]+)/);
      const method = methodMatch ? methodMatch[1].toUpperCase() : (/(\\s-d\\s|--data)/.test(lowered) ? 'POST' : 'GET');
      const urlMatch = text.match(/https?:\\/\\/[^'"\\s\\\\]+/);
      if (!urlMatch) return '';
      let parts = [];
      try {{
        parts = new URL(urlMatch[0]).pathname.split('/').filter(Boolean).filter(part => !['api', 'v1', 'v2', 'v3'].includes(part.toLowerCase()));
      }} catch (error) {{
        return '';
      }}
      const hasId = parts.length > 1 && /\\d|cm[a-z0-9]{{6,}}|[a-f0-9-]{{12,}}/i.test(parts[parts.length - 1]);
      let resource = hasId ? parts[parts.length - 2] : parts[parts.length - 1];
      resource = humanizeIdentifier(String(resource || 'operation').replace(/s$/, ''));
      if (method === 'GET') return hasId ? 'Get ' + resource + ' detail' : 'List ' + resource;
      if (method === 'POST') return 'Create ' + resource;
      if (method === 'PUT' || method === 'PATCH') return 'Update ' + resource;
      if (method === 'DELETE') return 'Delete ' + resource;
      return resource;
    }}
    function commandFromEvent(event) {{
      if (!event) return '';
      if (event.pending && event.pending.input) return event.pending.input.command || event.pending.input.pattern || event.pending.input.path || '';
      if (event.input) return event.input.command || event.input.pattern || event.input.path || '';
      return '';
    }}
    function commandTitle(event, command) {{
      const summary = summarizeCommand(command);
      if (summary) return summary;
      const pending = event.pending || event || {{}};
      return pendingOperation(pending) || summarizeCommand(command) || 'Command';
    }}
    function renderCommandDetails(node) {{
      const entries = commandDetailsByNode.get(node) || [];
      if (!entries.length) return;
      const bubble = node.querySelector('.bubble');
      bubble.querySelectorAll('.command-run-group').forEach(detail => detail.remove());
      const details = document.createElement('details');
      details.className = 'command-run-group';
      const detailsSummary = document.createElement('summary');
      const count = document.createElement('span');
      count.className = 'command-run-count';
      count.textContent = 'Ran ' + entries.length + ' command' + (entries.length === 1 ? '' : 's');
      detailsSummary.appendChild(count);
      details.appendChild(detailsSummary);
      const list = document.createElement('div');
      list.className = 'command-run-list';
      entries.forEach((entry, index) => {{
        const item = document.createElement('div');
        item.className = 'command-run-item';
        const title = document.createElement('div');
        title.className = 'command-run-title';
        title.textContent = (index + 1) + '. ' + (entry.title || 'Command');
        const pre = document.createElement('pre');
        pre.textContent = entry.command;
        item.appendChild(title);
        item.appendChild(pre);
        list.appendChild(item);
      }});
      details.appendChild(list);
      bubble.appendChild(details);
    }}
    function appendCommandSummary(event, targetNode = null) {{
      const command = commandFromEvent(event);
      if (!command) return targetNode;
      const key = String(command).trim();
      if (renderedCommandKeys.has(key)) return targetNode || commandSummaryNode;
      renderedCommandKeys.add(key);
      const node = targetNode || commandSummaryNode || addMessage('assistant', '');
      if (!targetNode) commandSummaryNode = node;
      const entries = commandDetailsByNode.get(node) || [];
      entries.push({{ title: commandTitle(event, command), command }});
      commandDetailsByNode.set(node, entries);
      renderCommandDetails(node);
      els.messages.scrollTop = els.messages.scrollHeight;
      return node;
    }}
    function setDrawer(name, open = true) {{
      document.querySelectorAll('.drawer-pane').forEach(pane => {{
        pane.classList.toggle('active', pane.dataset.pane === name);
      }});
      document.querySelectorAll('[data-drawer]').forEach(button => {{
        button.classList.toggle('active', open && button.dataset.drawer === name);
      }});
      els.drawerTitle.textContent = drawerTitles[name] || 'Workspace details';
      els.contextDrawer.classList.toggle('open', open);
      els.drawerBackdrop.classList.toggle('show', open);
    }}
    function closeDrawer() {{
      els.contextDrawer.classList.remove('open');
      els.drawerBackdrop.classList.remove('show');
      document.querySelectorAll('[data-drawer]').forEach(button => button.classList.remove('active'));
    }}
    function openAccountManager() {{
      setDrawer('accounts', true);
    }}
    async function post(url, body, timeoutMs = 0) {{
      const controller = timeoutMs ? new AbortController() : null;
      const timer = timeoutMs ? setTimeout(() => controller.abort(), timeoutMs) : null;
      let res;
      try {{
        res = await fetch(url, {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(body),
        signal: controller ? controller.signal : undefined
        }});
      }} catch (error) {{
        if (error && error.name === 'AbortError') throw new Error('Permission resolve timed out. Check the AgentBridge terminal logs and try again.');
        throw error;
      }} finally {{
        if (timer) clearTimeout(timer);
      }}
      const data = await res.json().catch(() => ({{ error: 'Request failed.' }}));
      if (!res.ok) throw new Error(data.error || 'Request failed.');
      return data;
    }}
    async function readStreamResponse(res, onEvent) {{
      if (!res.ok) {{
        const payload = await res.json().catch(() => ({{ error: 'Request failed.' }}));
        throw new Error(payload.error || 'Request failed.');
      }}
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {{
        const {{ value, done }} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {{ stream: true }});
        let boundary = buffer.indexOf('\\n\\n');
        while (boundary >= 0) {{
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const dataLine = frame.split('\\n').find(line => line.startsWith('data: '));
          if (dataLine) onEvent(JSON.parse(dataLine.slice(6)));
          boundary = buffer.indexOf('\\n\\n');
        }}
      }}
      buffer += decoder.decode();
      if (buffer.trim()) {{
        const dataLine = buffer.split('\\n').find(line => line.startsWith('data: '));
        if (dataLine) onEvent(JSON.parse(dataLine.slice(6)));
      }}
    }}
    function displayTextWithAttachments(text) {{
      if (!attachments.length) return text;
      const lines = attachments.map(file => '- ' + file.name + ' (' + file.size + ' bytes)');
      return (text || '').trim() + '\\n\\nAttached files:\\n' + lines.join('\\n');
    }}
    function setSending(sending) {{
      sendInFlight = sending;
      els.send.disabled = sending;
      els.send.hidden = sending;
      els.interrupt.hidden = !sending;
      els.send.setAttribute('aria-busy', sending ? 'true' : 'false');
    }}
    function clearVisibleIdleTimer() {{
      if (visibleIdleTimer) clearTimeout(visibleIdleTimer);
      visibleIdleTimer = null;
    }}
    function startVisibleIdleTimer() {{
      clearVisibleIdleTimer();
      visibleIdleTimer = setTimeout(() => {{
        if (!sendInFlight && !awaitingAuthorization) return;
        if (activeStreamController) activeStreamController.abort();
        addMessage('assistant', 'AI agent request timed out after 20 seconds without visible progress.');
        setAwaitingAuthorization(false);
        setSending(false);
      }}, STREAM_VISIBLE_IDLE_TIMEOUT_MS);
    }}
    function markVisibleStreamProgress() {{
      if (sendInFlight) startVisibleIdleTimer();
    }}
    function setAwaitingAuthorization(awaiting) {{
      awaitingAuthorization = awaiting;
      if (awaiting) {{
        clearVisibleIdleTimer();
        sendInFlight = false;
        els.send.disabled = true;
        els.send.hidden = false;
        els.interrupt.hidden = true;
        els.send.setAttribute('aria-busy', 'false');
      }} else if (!sendInFlight) {{
        els.send.disabled = false;
        els.send.hidden = false;
        els.interrupt.hidden = true;
        els.send.setAttribute('aria-busy', 'false');
      }}
    }}
    async function sendMessage(text) {{
      if (sendInFlight || awaitingAuthorization) return;
      if (!text.trim() && !attachments.length) return;
      if (!validRuntimeBaseUrl(true)) {{
        els.baseUrl.focus();
        return;
      }}
      try {{
        setAwaitingAuthorization(false);
        commandSummaryNode = null;
        renderedCommandKeys.clear();
        addMessage('user', displayTextWithAttachments(text));
        const outgoingAttachments = attachments;
        els.message.value = '';
        attachments = [];
        renderAttachments();
        renderCommandMenu();
        setSending(true);
        startVisibleIdleTimer();
        let assistantNode = null;
        let assistantText = '';
        let sawDone = false;
        activeStreamController = new AbortController();
        const res = await fetch('/api/chat/stream', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(payload({{ message: text, attachments: outgoingAttachments }})),
          signal: activeStreamController.signal
        }});
        await readStreamResponse(res, event => {{
          renderTimelineEvent(event);
          if (event.type === 'assistant_text' || event.type === 'assistant_text_delta') {{
            assistantText += event.text || '';
            if (!assistantNode) assistantNode = addMessage('assistant', '');
            updateAssistantMessage(assistantNode, assistantText);
            markVisibleStreamProgress();
          }} else if (event.type === 'confirmation_required') {{
            renderPending(event.pending);
            assistantNode = appendCommandSummary(event, assistantNode) || assistantNode;
            setSending(false);
            setAwaitingAuthorization(true);
            if (event.message && !assistantNode && !commandFromEvent(event)) assistantNode = addMessage('assistant', event.message);
          }} else if (event.type === 'tool_use') {{
            if (commandFromEvent(event)) {{
              assistantNode = appendCommandSummary(event, assistantNode) || assistantNode;
              markVisibleStreamProgress();
            }}
          }} else if (event.type === 'tool_result' && event.message && !assistantNode) {{
            assistantNode = addMessage('assistant', event.message);
            markVisibleStreamProgress();
          }} else if (event.type === 'tools') {{
            renderTools(event.tools || []);
            if (event.message && !assistantNode) {{
              assistantNode = addMessage('assistant', event.message);
              markVisibleStreamProgress();
            }}
          }} else if (event.type === 'usage') {{
            renderUsage(event.usage || {{}});
          }} else if (event.type === 'error') {{
            clearVisibleIdleTimer();
            addMessage('assistant', event.message || 'Request failed.');
            sawDone = true;
            setAwaitingAuthorization(false);
            setSending(false);
          }} else if (event.type === 'interrupted') {{
            clearVisibleIdleTimer();
            addMessage('assistant', event.message || 'Current Agent request interrupted.');
            sawDone = true;
            setAwaitingAuthorization(false);
            setSending(false);
          }} else if (event.type === 'done') {{
            clearVisibleIdleTimer();
            sawDone = true;
            if (event.pending) renderPending(event.pending);
            if (event.usage) renderUsage(event.usage);
            if (event.tools && event.tools.length) renderTools(event.tools);
            if (event.conversations) renderConversations(event.conversations);
            if (!assistantText && event.message && !assistantNode) assistantNode = addMessage('assistant', event.message);
            setAwaitingAuthorization(false);
            setSending(false);
          }}
        }});
        if (!sawDone) addMessage('assistant', 'Request ended before AgentBridge returned a response.');
      }} catch (error) {{
        if (!(error && error.name === 'AbortError')) {{
          addMessage('assistant', 'Request failed: ' + (error && error.message ? error.message : error));
        }}
      }} finally {{
        activeStreamController = null;
        clearVisibleIdleTimer();
        setAwaitingAuthorization(false);
        setSending(false);
      }}
    }}
    async function interruptRequest() {{
      if (!sendInFlight) return;
      try {{
        await post('/api/chat/interrupt', payload());
      }} catch (error) {{
        // The in-flight stream may already be closing.
      }}
      if (activeStreamController) activeStreamController.abort();
      clearVisibleIdleTimer();
      addMessage('assistant', 'Current Agent request interrupted.');
      setAwaitingAuthorization(false);
      setSending(false);
    }}
    function renderChatResponse(data) {{
      if (!data) return;
      if (data.pending) renderPending(data.pending);
      else renderPending(null);
      if (data.usage) renderUsage(data.usage);
      if (data.tools && data.tools.length) renderTools(data.tools);
      if (data.message) addMessage('assistant', data.message);
    }}
    function renderTimelineEvent(event) {{
      return;
    }}
    function renderPending(pending) {{
      if (!pending) {{
        currentPending = null;
        setPendingBusy(false);
        els.pending.classList.remove('show');
        els.pendingSummary.textContent = '';
        els.pendingText.textContent = '';
        els.pendingCommand.textContent = '';
        els.pendingDetails.hidden = true;
        return;
      }}
      currentPending = pending;
      els.pending.classList.add('show');
      els.pendingDetails.open = false;
      els.pendingDetails.hidden = true;
      els.pendingCommand.textContent = '';
      if (pending.kind === 'agent_permission') {{
        const input = pending.input || {{}};
        const command = input.command || input.pattern || input.path || '';
        els.pendingSummary.textContent = pendingOperation(pending);
        els.pendingText.textContent = (pending.display_name || pending.tool || 'Agent tool') + (pending.tool ? ' · ' + pending.tool : '');
        if (command) {{
          els.pendingCommand.textContent = command;
          els.pendingDetails.hidden = false;
        }}
        return;
      }}
      const plan = pending.plan || {{}};
      const transport = plan.transport || {{}};
      const preview = plan.request_preview || {{}};
      els.pendingSummary.textContent = pendingOperation(pending);
      els.pendingText.textContent = plan.risk + ' · ' + (preview.method || transport.method || transport.type || '') + ' ' + (preview.url || transport.path || '');
      const command = JSON.stringify({{ arguments: pending.args || {{}}, request: preview }}, null, 2);
      els.pendingCommand.textContent = command;
      els.pendingDetails.hidden = false;
    }}
    function setPendingBusy(busy, message = '') {{
      els.confirm.disabled = busy;
      els.cancel.disabled = busy;
      if (busy && message) els.pendingText.textContent = message;
    }}
    async function resolvePending(allow) {{
      if (!currentPending) return;
      const pending = currentPending;
      if (!allow) {{
        renderPending(null);
        setAwaitingAuthorization(false);
        setSending(false);
      }} else {{
        setPendingBusy(true, 'Authorizing...');
      }}
      try {{
        if (pending.kind === 'agent_permission') {{
          const data = await post('/api/chat/agent-permission', payload({{ permission_id: pending.id, allow }}), allow ? 15000 : 5000);
          if (data.error || data.status === 'not_found') throw new Error(data.error || data.message || 'No matching permission request is pending.');
          renderPending(null);
          if (!allow && activeStreamController) activeStreamController.abort();
          return;
        }}
        const data = await post('/api/chat/pending', payload({{ allow }}));
        if (data.error) throw new Error(data.error);
        renderChatResponse(data);
      }} catch (error) {{
        if (!allow) {{
          try {{
            await post('/api/chat/interrupt', payload(), 5000);
          }} catch (interruptError) {{
            // The permission stream may already be closed or unreachable.
          }}
          if (activeStreamController) activeStreamController.abort();
          return;
        }}
        const message = 'Authorization request failed: ' + (error && error.message ? error.message : error);
        if (currentPending && currentPending.id === pending.id) els.pendingText.textContent = message;
        addMessage('assistant', message);
      }} finally {{
        if (currentPending && currentPending.id === pending.id) setPendingBusy(false);
      }}
    }}
    function formatNumber(value) {{
      return new Intl.NumberFormat().format(Number(value || 0));
    }}
    function renderUsage(usage = {{}}) {{
      els.usageInput.textContent = formatNumber(usage.input_tokens);
      els.usageOutput.textContent = formatNumber(usage.output_tokens);
      els.usageTotal.textContent = formatNumber(usage.total_tokens);
      els.usageSessionTotal.textContent = formatNumber(usage.session_total_tokens);
      els.usageHistory.innerHTML = '';
      const history = Array.isArray(usage.history) ? usage.history.slice(-100).reverse() : [];
      if (!history.length) {{
        const empty = document.createElement('div');
        empty.className = 'subtle';
        empty.textContent = 'Usage appears after an AI response.';
        els.usageHistory.appendChild(empty);
        return;
      }}
      history.forEach((item, index) => {{
        const row = document.createElement('div');
        row.className = 'usage-history-item';
        const left = document.createElement('span');
        left.textContent = 'Turn ' + (history.length - index);
        const right = document.createElement('span');
        right.textContent = 'in ' + formatNumber(item.input_tokens) + ' · out ' + formatNumber(item.output_tokens) + ' · total ' + formatNumber(item.total_tokens);
        row.appendChild(left);
        row.appendChild(right);
        els.usageHistory.appendChild(row);
      }});
    }}
    function renderLoginAccounts(accounts = [], selectedId = '') {{
      loginAccountsCache = accounts;
      const previous = els.loginAccount.value;
      els.loginAccount.innerHTML = '';
      const empty = document.createElement('option');
      empty.value = '';
      empty.textContent = accounts.length ? 'No saved account' : 'No saved accounts';
      els.loginAccount.appendChild(empty);
      accounts.forEach(account => {{
        const option = document.createElement('option');
        option.value = account.id || '';
        option.textContent = account.label || account.id || 'Saved account';
        els.loginAccount.appendChild(option);
      }});
      const target = selectedId || previous;
      if (target && accounts.some(account => account.id === target)) {{
        els.loginAccount.value = target;
      }} else {{
        els.loginAccount.value = '';
      }}
      els.loginAccount.disabled = accounts.length === 0;
      els.loginAccount.title = accounts.length ? 'Saved account for login tools' : 'Run a login tool once to save an account';
      renderAccountList();
      if (editingAccountId && !accounts.some(account => account.id === editingAccountId)) closeAccountEditor();
    }}
    function selectedAccount() {{
      return loginAccountsCache.find(account => account.id === selectedLoginAccountId()) || null;
    }}
    function renderAccountList() {{
      els.accountList.innerHTML = '';
      if (!loginAccountsCache.length) {{
        const empty = document.createElement('div');
        empty.className = 'subtle';
        empty.textContent = 'No saved accounts.';
        els.accountList.appendChild(empty);
        return;
      }}
      loginAccountsCache.forEach(account => {{
        const row = document.createElement('div');
        row.className = 'account-row';
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'account-card' + (account.id === selectedLoginAccountId() ? ' active' : '');
        button.innerHTML = '<strong></strong><span class="subtle"></span>';
        button.querySelector('strong').textContent = account.label || account.id || 'Saved account';
        button.querySelector('.subtle').textContent = account.username || account.id || '';
        button.onclick = () => selectLoginAccount(account.id);
        const menu = document.createElement('button');
        menu.type = 'button';
        menu.className = 'account-menu';
        menu.title = 'Account actions';
        menu.textContent = '...';
        const popover = document.createElement('div');
        popover.className = 'account-popover';
        popover.hidden = true;
        const editButton = document.createElement('button');
        editButton.type = 'button';
        editButton.textContent = 'Edit';
        const deleteButton = document.createElement('button');
        deleteButton.type = 'button';
        deleteButton.className = 'danger-text';
        deleteButton.textContent = 'Delete';
        popover.appendChild(editButton);
        popover.appendChild(deleteButton);
        menu.onclick = (event) => {{
          event.stopPropagation();
          document.querySelectorAll('.account-popover').forEach(node => {{
            if (node !== popover) node.hidden = true;
          }});
          popover.hidden = !popover.hidden;
        }};
        editButton.onclick = (event) => {{
          event.stopPropagation();
          popover.hidden = true;
          openAccountEditor(account);
        }};
        deleteButton.onclick = (event) => {{
          event.stopPropagation();
          popover.hidden = true;
          deleteLoginAccount(account.id);
        }};
        row.appendChild(button);
        row.appendChild(menu);
        row.appendChild(popover);
        els.accountList.appendChild(row);
      }});
    }}
    async function selectLoginAccount(accountId) {{
      if (!accountId) return;
      els.loginAccount.value = accountId;
      const data = await post('/api/login-account', payload({{ action: 'select', account_id: accountId }}));
      if (data.runtime) {{
        renderLoginAccounts(data.runtime.login_accounts || [], data.runtime.selected_login_account || '');
        setConnectionStatus('Selected saved account.', 'success');
      }}
    }}
    function openAccountEditor(account = null) {{
      editingAccountId = account ? (account.id || '') : '';
      els.accountForm.hidden = false;
      els.accountEditor.textContent = account ? 'Edit account' : 'New account';
      els.accountLabel.value = account ? (account.label || '') : '';
      els.accountUsername.value = account ? (account.username || account.label || '') : '';
      els.accountPassword.value = '';
      els.saveAccount.textContent = account ? 'Save changes' : 'Save new account';
      els.accountUsername.focus();
    }}
    function closeAccountEditor() {{
      editingAccountId = '';
      els.accountForm.hidden = true;
      els.accountLabel.value = '';
      els.accountUsername.value = '';
      els.accountPassword.value = '';
    }}
    function accountPayload(action, extra = {{}}) {{
      return payload(Object.assign({{
        action,
        account_id: editingAccountId,
        label: els.accountLabel.value.trim(),
        username: els.accountUsername.value.trim(),
        password: els.accountPassword.value
      }}, extra));
    }}
    async function saveAccountForm(event) {{
      if (event) event.preventDefault();
      const data = await post('/api/login-account', accountPayload('upsert'));
      if (data.runtime) {{
        renderLoginAccounts(data.runtime.login_accounts || [], data.runtime.selected_login_account || '');
        closeAccountEditor();
        setConnectionStatus('Saved account updated.', 'success');
      }}
    }}
    function newAccountForm() {{
      openAccountEditor(null);
    }}
    async function deleteLoginAccount(accountId) {{
      if (!accountId) return;
      const data = await post('/api/login-account', payload({{ action: 'delete', account_id: accountId }}));
      if (data.runtime) {{
        renderLoginAccounts(data.runtime.login_accounts || [], data.runtime.selected_login_account || '');
        if (editingAccountId === accountId) closeAccountEditor();
        setConnectionStatus('Saved account deleted.', 'success');
      }}
    }}
    function buildToolCommand(tool) {{
      const params = (tool.required || []).map(name => name + '=');
      return '/run ' + tool.name + (params.length ? ' ' + params.join(' ') : '');
    }}
    function insertToolCommand(tool) {{
      els.message.value = buildToolCommand(tool);
      els.message.dispatchEvent(new Event('input'));
      els.message.focus();
      const firstValue = els.message.value.indexOf('=');
      if (firstValue >= 0) els.message.setSelectionRange(firstValue + 1, firstValue + 1);
    }}
    function renderTools(tools) {{
      toolsCache = tools;
      els.tools.innerHTML = '';
      tools.forEach(tool => {{
        const node = document.createElement('button');
        node.type = 'button';
        node.className = 'tool-button';
        node.innerHTML = '<strong></strong><div class="subtle"></div><div class="tool-params"></div>';
        node.querySelector('strong').textContent = tool.name + ' [' + tool.risk + ']';
        node.querySelector('.subtle').textContent = tool.description || '';
        const params = node.querySelector('.tool-params');
        const required = tool.required || [];
        if (!required.length) {{
          const chip = document.createElement('span');
          chip.className = 'param-chip';
          chip.textContent = 'No required parameters';
          params.appendChild(chip);
        }} else {{
          required.forEach(name => {{
            const schema = (tool.property_schemas || {{}})[name] || {{}};
            const chip = document.createElement('span');
            chip.className = 'param-chip';
            chip.textContent = name + (schema.type ? ' · ' + schema.type : '');
            chip.title = schema.description || name;
            params.appendChild(chip);
          }});
        }}
        node.onclick = () => insertToolCommand(tool);
        els.tools.appendChild(node);
      }});
      renderCommandMenu();
    }}
    function renderCommandMenu() {{
      const raw = els.message.value;
      const text = raw.trimStart().toLowerCase();
      const items = [];
      const baseCommands = [
        {{ command: '/tools', title: '/tools', detail: 'List tools in the parsed system layer' }},
        {{ command: '/run ', title: '/run', detail: 'Run a generated tool with key=value arguments' }},
        {{ command: 'confirm', title: 'confirm', detail: 'Approve the pending high-risk operation' }},
        {{ command: 'cancel', title: 'cancel', detail: 'Clear the pending operation' }}
      ];
      if (text.startsWith('/run')) {{
        toolsCache.slice(0, 12).forEach(tool => items.push({{
          command: buildToolCommand(tool),
          title: tool.name,
          detail: (tool.required || []).length
            ? 'Required parameters: ' + tool.required.join(', ')
            : 'No required parameters'
        }}));
      }} else if (text.startsWith('/')) {{
        baseCommands
          .filter(item => item.command.startsWith(text) || item.title.startsWith(text))
          .forEach(item => items.push(item));
      }}
      els.commandMenu.innerHTML = '';
      items.forEach(item => {{
        const button = document.createElement('button');
        button.className = 'suggestion';
        button.dataset.command = item.command;
        button.innerHTML = '<strong></strong><span class="subtle"></span>';
        button.querySelector('strong').textContent = item.title;
        button.querySelector('.subtle').textContent = item.detail;
        button.addEventListener('mousedown', event => {{
          event.preventDefault();
          els.message.value = item.command;
          els.message.focus();
          renderCommandMenu();
        }});
        els.commandMenu.appendChild(button);
      }});
      els.commandMenu.classList.toggle('show', items.length > 0);
    }}
    function renderAttachments() {{
      els.attachments.innerHTML = '';
      attachments.forEach((file, index) => {{
        const chip = document.createElement('span');
        chip.className = 'attachment';
        chip.innerHTML = '<span></span><button type="button">x</button>';
        chip.querySelector('span').textContent = file.name + ' · ' + file.size + ' bytes';
        chip.querySelector('button').onclick = () => {{
          attachments.splice(index, 1);
          renderAttachments();
        }};
        els.attachments.appendChild(chip);
      }});
    }}
    function renderConversations(conversations) {{
      els.conversations.innerHTML = '';
      if (!conversations.length) {{
        const empty = document.createElement('div');
        empty.className = 'subtle';
        empty.textContent = 'No recent conversations.';
        els.conversations.appendChild(empty);
        return;
      }}
      conversations.forEach(item => {{
        const row = document.createElement('div');
        row.className = 'conversation-row';
        const button = document.createElement('button');
        button.className = 'conversation' + (item.session_id === els.session.value ? ' active' : '');
        button.innerHTML = '<strong></strong><span class="subtle"></span>';
        button.querySelector('strong').textContent = item.title || item.session_id;
        button.querySelector('.subtle').textContent = item.preview || (item.message_count + ' messages');
        button.onclick = () => {{
          els.session.value = item.session_id;
          loadState();
        }};
        const menu = document.createElement('button');
        menu.type = 'button';
        menu.className = 'conversation-menu';
        menu.title = 'Conversation actions';
        menu.textContent = '...';
        const popover = document.createElement('div');
        popover.className = 'conversation-popover';
        popover.hidden = true;
        const renameButton = document.createElement('button');
        renameButton.type = 'button';
        renameButton.textContent = 'Rename';
        const deleteButton = document.createElement('button');
        deleteButton.type = 'button';
        deleteButton.className = 'danger-text';
        deleteButton.textContent = 'Delete';
        popover.appendChild(renameButton);
        popover.appendChild(deleteButton);
        menu.onclick = (event) => {{
          event.stopPropagation();
          document.querySelectorAll('.conversation-popover').forEach(node => {{
            if (node !== popover) node.hidden = true;
          }});
          popover.hidden = !popover.hidden;
        }};
        renameButton.onclick = (event) => {{
          event.stopPropagation();
          popover.hidden = true;
          renameConversation(item.session_id, item.title || item.session_id);
        }};
        deleteButton.onclick = (event) => {{
          event.stopPropagation();
          popover.hidden = true;
          deleteConversation(item.session_id);
        }};
        row.appendChild(button);
        row.appendChild(menu);
        row.appendChild(popover);
        els.conversations.appendChild(row);
      }});
    }}
    async function renameConversation(sessionId, currentTitle) {{
      const title = window.prompt('Rename conversation', currentTitle || sessionId);
      if (!title) return;
      const data = await post('/api/conversation', payload({{ action: 'rename', session_id: sessionId, title }}));
      renderConversations(data.conversations || []);
    }}
    async function deleteConversation(sessionId) {{
      const confirmed = window.confirm('Delete this conversation?');
      if (!confirmed) return;
      const data = await post('/api/conversation', payload({{ action: 'delete', session_id: sessionId }}));
      if (els.session.value === sessionId) startNewChat(false);
      renderConversations(data.conversations || []);
    }}
    function startNewChat(close = true) {{
      const now = new Date();
      const stamp = now.toISOString().replace(/[-:]/g, '').slice(0, 15);
      els.session.value = 'chat-' + stamp;
      els.messages.innerHTML = '';
      renderPending(null);
      if (close) closeDrawer();
      loadState();
    }}
    async function loadConversations() {{
      const qs = new URLSearchParams({{ user: els.user.value }});
      if (allowKitSwitch) qs.set('kit', els.kit.value);
      const data = await fetch('/api/conversations?' + qs.toString()).then(r => r.json());
      renderConversations(data.conversations || []);
    }}
    function canReadFileText(file) {{
      return file.size <= 65536 && (
        file.type.startsWith('text/') ||
        file.name.endsWith('.md') ||
        file.name.endsWith('.json') ||
        file.name.endsWith('.csv') ||
        file.name.endsWith('.txt')
      );
    }}
    async function loadState(includeRuntime = false) {{
      if (runtimeExecute && els.baseUrl.value.trim() && !validRuntimeBaseUrl(false)) return;
      const data = await fetch('/api/state?' + stateQuery(includeRuntime).toString()).then(r => r.json());
      if (data.error) {{
        setConnectionStatus(data.error, 'error');
        return;
      }}
      if (data.runtime) {{
        if (data.runtime.base_url) els.baseUrl.value = data.runtime.base_url;
        applyRuntimeMode(data.runtime.execute ? 'execute' : 'dry-run', false);
        renderLoginAccounts(data.runtime.login_accounts || [], data.runtime.selected_login_account || '');
      }}
      els.messages.innerHTML = '';
      (data.history || []).forEach(item => addMessage(item.role, item.content));
      renderPending(data.pending);
      renderTools(data.tools || []);
      renderConversations(data.conversations || []);
      renderUsage(data.usage || {{}});
    }}
    document.getElementById('send').onclick = () => sendMessage(els.message.value);
    document.getElementById('interruptBtn').onclick = interruptRequest;
    document.getElementById('attachBtn').onclick = () => els.fileInput.click();
    els.runtimeMode.querySelectorAll('[data-mode]').forEach(button => {{
      button.addEventListener('click', () => applyRuntimeMode(button.dataset.mode));
    }});
    els.testConnection.onclick = testConnectivity;
    els.manageAccounts.onclick = openAccountManager;
    els.accountForm.addEventListener('submit', saveAccountForm);
    els.newAccountForm.onclick = newAccountForm;
    els.cancelAccountEdit.onclick = closeAccountEditor;
    els.drawerNewChat.onclick = () => startNewChat(false);
    els.baseUrl.addEventListener('input', () => setConnectionStatus());
    els.baseUrl.addEventListener('change', () => {{
      if (runtimeExecute && validRuntimeBaseUrl(true)) loadState(true);
    }});
    els.loginAccount.addEventListener('change', () => {{
      loadState(true);
    }});
    document.querySelectorAll('[data-drawer]').forEach(button => {{
      button.addEventListener('click', () => {{
        const isSameOpenDrawer = els.contextDrawer.classList.contains('open') &&
          document.querySelector('.drawer-pane.active')?.dataset.pane === button.dataset.drawer;
        if (isSameOpenDrawer) closeDrawer();
        else setDrawer(button.dataset.drawer, true);
      }});
    }});
    document.getElementById('mobileMenuBtn').onclick = () => setDrawer('conversations', true);
    document.getElementById('drawerCloseBtn').onclick = closeDrawer;
    els.drawerBackdrop.onclick = closeDrawer;
    document.getElementById('newChatBtn').onclick = () => startNewChat(true);
    els.fileInput.addEventListener('change', async () => {{
      const selected = [];
      for (const file of Array.from(els.fileInput.files || [])) {{
        const item = {{
          name: file.name,
          size: file.size,
          type: file.type || 'file'
        }};
        if (canReadFileText(file)) {{
          try {{
            item.content = await file.text();
          }} catch (error) {{
            item.read_error = 'Could not read file text.';
          }}
        }}
        selected.push(item);
      }}
      attachments = attachments.concat(selected);
      els.fileInput.value = '';
      renderAttachments();
    }});
    els.message.addEventListener('input', () => {{
      renderCommandMenu();
      els.message.style.height = 'auto';
      els.message.style.height = Math.min(180, els.message.scrollHeight) + 'px';
    }});
    els.message.addEventListener('blur', () => setTimeout(() => els.commandMenu.classList.remove('show'), 120));
    els.message.addEventListener('focus', renderCommandMenu);
    els.message.addEventListener('keydown', (event) => {{
      if (event.key === 'Enter' && !event.shiftKey) {{
        event.preventDefault();
        sendMessage(els.message.value);
      }} else if (event.key === 'Escape') {{
        els.commandMenu.classList.remove('show');
      }}
    }});
    document.getElementById('confirmBtn').onclick = () => resolvePending(true);
    document.getElementById('cancelBtn').onclick = () => resolvePending(false);
    [els.user, els.session, els.kit].forEach(el => el.addEventListener('change', () => {{
      loadState();
      loadConversations();
    }}));
    applyRuntimeMode(initialExecuteMode ? 'execute' : 'dry-run', false);
    loadState(false);
    loadConversations();
  </script>
</body>
</html>"""


def escape_attr(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
