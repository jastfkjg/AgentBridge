from __future__ import annotations

import json
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from agentbridge.chat import ChatConfig, ChatSession


class ChatWebError(ValueError):
    pass


def run_web_chat(config: ChatConfig, host: str = "127.0.0.1", port: int = 8765, allow_kit_switch: bool = False) -> int:
    handler = build_handler(config, allow_kit_switch=allow_kit_switch)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"AgentBridge Web Chat: http://{host}:{server.server_port}", flush=True)
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
                        "pending": session.pending.to_dict() if session.pending else None,
                        "tools": session.tool_summaries(),
                        "conversations": conversation_summaries(session.config, session.config.user, session.config.kit_dir),
                    }
                )
                return
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path not in {"/api/chat", "/api/tool"}:
                self._send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            try:
                body = self._read_json()
                session = self._session_from_body(body)
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
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

        def _session_from_query(self, query: str) -> ChatSession:
            values = parse_qs(query)
            user = values.get("user", [base_config.user])[0] or base_config.user
            session_id = values.get("session", [base_config.session_id])[0] or base_config.session_id
            kit_dir = values.get("kit", [str(base_config.kit_dir)])[0] if allow_kit_switch else str(base_config.kit_dir)
            return get_session(user=user, session_id=session_id, kit_dir=Path(kit_dir))

        def _session_from_body(self, body: dict[str, Any]) -> ChatSession:
            user = str(body.get("user") or base_config.user)
            session_id = str(body.get("session_id") or base_config.session_id)
            kit_dir = Path(str(body.get("kit_dir") or base_config.kit_dir)) if allow_kit_switch else base_config.kit_dir
            return get_session(user=user, session_id=session_id, kit_dir=kit_dir)

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

        def _send_error(self, status: HTTPStatus, message: str) -> None:
            self._send_json({"error": message}, status=status)

        def log_message(self, format: str, *args: Any) -> None:
            return

    def get_session(user: str, session_id: str, kit_dir: Path) -> ChatSession:
        key = f"{user}:{session_id}:{kit_dir}"
        if key not in sessions:
            sessions[key] = ChatSession(replace(base_config, user=user, session_id=session_id, kit_dir=kit_dir))
        return sessions[key]

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
        "Choose any generated kit directory for this session."
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
    input, textarea, button {{
      font: inherit;
      letter-spacing: 0;
    }}
    input, textarea {{
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
    .pending {{
      margin-top: 18px;
      border-left: 3px solid var(--danger);
      padding-left: 12px;
      display: none;
    }}
    .pending.show {{
      display: block;
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
      }}
      .header-meta {{
        display: none;
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
            <div class="header-meta">Chat with the active integration kit</div>
          </div>
        </div>
        <div class="mode">{'Execute' if config.execute else 'Dry-run'} mode</div>
      </div>
      <div class="messages message-stream reading-column" id="messages" aria-live="polite"></div>
      <div class="composer composer-dock">
        <div class="composer-shell composer-card">
          <div class="command-menu" id="commandMenu">
            <button class="suggestion" data-command="/tools"><strong>/tools</strong><span class="subtle">List tools in the active kit</span></button>
            <button class="suggestion" data-command="/run"><strong>/run</strong><span class="subtle">Run a generated tool with key=value arguments</span></button>
            <button class="suggestion" data-command="confirm"><strong>confirm</strong><span class="subtle">Approve the pending high-risk operation</span></button>
            <button class="suggestion" data-command="cancel"><strong>cancel</strong><span class="subtle">Clear the pending operation</span></button>
          </div>
          <div class="attachments" id="attachments"></div>
          <div class="composer-row">
            <input id="fileInput" type="file" multiple hidden>
            <textarea id="message" placeholder="Ask the agent to operate the system..."></textarea>
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
        <section class="drawer-pane" data-pane="tools">
          <div class="subtle">Tools loaded from the active kit.</div>
          <div class="tools" id="tools"></div>
        </section>
        <div class="pending" id="pending">
          <strong>Confirmation required</strong>
          <div class="subtle" id="pendingText"></div>
          <div class="actions">
            <button id="confirmBtn">Confirm</button>
            <button class="secondary" id="cancelBtn">Cancel</button>
          </div>
        </div>
      </div>
    </aside>
    <button class="drawer-backdrop" id="drawerBackdrop" type="button" aria-label="Close navigation"></button>
  </div>
  <script src="/assets/markdown-it.min.js"></script>
  <script>
    const allowKitSwitch = {allow_switch};
    const executeMode = {execute};
    const els = {{
      user: document.getElementById('user'),
      session: document.getElementById('session'),
      kit: document.getElementById('kit'),
      messages: document.getElementById('messages'),
      message: document.getElementById('message'),
      send: document.getElementById('send'),
      fileInput: document.getElementById('fileInput'),
      attachments: document.getElementById('attachments'),
      commandMenu: document.getElementById('commandMenu'),
      conversations: document.getElementById('conversations'),
      tools: document.getElementById('tools'),
      pending: document.getElementById('pending'),
      pendingText: document.getElementById('pendingText'),
      contextDrawer: document.getElementById('contextDrawer'),
      drawerTitle: document.getElementById('drawerTitle'),
      drawerBackdrop: document.getElementById('drawerBackdrop')
    }};
    let toolsCache = [];
    let attachments = [];
    let sendInFlight = false;
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
      tools: 'Available tools'
    }};
    function payload(extra = {{}}) {{
      return Object.assign({{
        user: els.user.value,
        session_id: els.session.value,
        kit_dir: allowKitSwitch ? els.kit.value : undefined
      }}, extra);
    }}
    function stateQuery() {{
      const qs = new URLSearchParams({{ user: els.user.value, session: els.session.value }});
      if (allowKitSwitch) qs.set('kit', els.kit.value);
      return qs;
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
    async function post(url, body) {{
      const res = await fetch(url, {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(body)
      }});
      return await res.json();
    }}
    function displayTextWithAttachments(text) {{
      if (!attachments.length) return text;
      const lines = attachments.map(file => '- ' + file.name + ' (' + file.size + ' bytes)');
      return (text || '').trim() + '\\n\\nAttached files:\\n' + lines.join('\\n');
    }}
    function setSending(sending) {{
      sendInFlight = sending;
      els.send.disabled = sending;
      els.send.setAttribute('aria-busy', sending ? 'true' : 'false');
    }}
    async function sendMessage(text) {{
      if (sendInFlight) return;
      if (!text.trim() && !attachments.length) return;
      setSending(true);
      try {{
        addMessage('user', displayTextWithAttachments(text));
        const outgoingAttachments = attachments;
        els.message.value = '';
        attachments = [];
        renderAttachments();
        renderCommandMenu();
        const data = await post('/api/chat', payload({{ message: text, attachments: outgoingAttachments }}));
        if (data.error) {{
          addMessage('assistant', data.error);
          return;
        }}
        addMessage('assistant', data.message);
        renderPending(data.pending);
        if (data.tools && data.tools.length) renderTools(data.tools);
        if (data.conversations) renderConversations(data.conversations);
      }} catch (error) {{
        addMessage('assistant', 'Request failed: ' + (error && error.message ? error.message : error));
      }} finally {{
        setSending(false);
      }}
    }}
    function renderPending(pending) {{
      if (!pending) {{
        els.pending.classList.remove('show');
        return;
      }}
      els.pending.classList.add('show');
      const plan = pending.plan || {{}};
      const transport = plan.transport || {{}};
      els.pendingText.textContent = pending.tool + ' · ' + plan.risk + ' · ' + (transport.method || transport.type || '') + ' ' + (transport.path || '');
    }}
    function renderTools(tools) {{
      toolsCache = tools;
      els.tools.innerHTML = '';
      tools.forEach(tool => {{
        const node = document.createElement('div');
        node.className = 'tool';
        node.innerHTML = '<strong></strong><div class="subtle"></div>';
        node.querySelector('strong').textContent = tool.name + ' [' + tool.risk + ']';
        node.querySelector('.subtle').textContent = 'Required: ' + ((tool.required || []).join(', ') || 'none');
        els.tools.appendChild(node);
      }});
      renderCommandMenu();
    }}
    function renderCommandMenu() {{
      const raw = els.message.value;
      const text = raw.trimStart().toLowerCase();
      const items = [];
      const baseCommands = [
        {{ command: '/tools', title: '/tools', detail: 'List tools in the active kit' }},
        {{ command: '/run ', title: '/run', detail: 'Run a generated tool with key=value arguments' }},
        {{ command: 'confirm', title: 'confirm', detail: 'Approve the pending high-risk operation' }},
        {{ command: 'cancel', title: 'cancel', detail: 'Clear the pending operation' }}
      ];
      if (text.startsWith('/run')) {{
        toolsCache.slice(0, 12).forEach(tool => items.push({{
          command: '/run ' + tool.name + ' ',
          title: tool.name,
          detail: 'Required: ' + ((tool.required || []).join(', ') || 'none')
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
        const button = document.createElement('button');
        button.className = 'conversation' + (item.session_id === els.session.value ? ' active' : '');
        button.innerHTML = '<strong></strong><span class="subtle"></span>';
        button.querySelector('strong').textContent = item.session_id;
        button.querySelector('.subtle').textContent = item.preview || (item.message_count + ' messages');
        button.onclick = () => {{
          els.session.value = item.session_id;
          loadState();
        }};
        els.conversations.appendChild(button);
      }});
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
    async function loadState() {{
      const data = await fetch('/api/state?' + stateQuery().toString()).then(r => r.json());
      els.messages.innerHTML = '';
      (data.history || []).forEach(item => addMessage(item.role, item.content));
      renderPending(data.pending);
      renderTools(data.tools || []);
      renderConversations(data.conversations || []);
    }}
    document.getElementById('send').onclick = () => sendMessage(els.message.value);
    document.getElementById('attachBtn').onclick = () => els.fileInput.click();
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
    document.getElementById('newChatBtn').onclick = () => {{
      const now = new Date();
      const stamp = now.toISOString().replace(/[-:]/g, '').slice(0, 15);
      els.session.value = 'chat-' + stamp;
      els.messages.innerHTML = '';
      els.pending.classList.remove('show');
      closeDrawer();
      loadState();
    }};
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
    document.getElementById('confirmBtn').onclick = () => sendMessage('confirm');
    document.getElementById('cancelBtn').onclick = () => sendMessage('cancel');
    [els.user, els.session, els.kit].forEach(el => el.addEventListener('change', () => {{
      loadState();
      loadConversations();
    }}));
    loadState();
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
