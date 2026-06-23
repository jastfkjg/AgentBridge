from __future__ import annotations

import json
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
            data = html.encode("utf-8")
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", "text/html; charset=utf-8")
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
      --ink: #17201b;
      --muted: #66736c;
      --line: #d9e0dc;
      --surface: #f7f8f5;
      --panel: #ffffff;
      --accent: #0f7b63;
      --danger: #a43d3d;
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
    .app {{
      height: 100svh;
      display: grid;
      grid-template-columns: minmax(240px, 320px) 1fr minmax(260px, 360px);
      overflow: hidden;
    }}
    aside, main {{
      min-width: 0;
      min-height: 0;
    }}
    .left, .right {{
      padding: 22px;
      border-right: 1px solid var(--line);
      background: #fbfcfa;
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
      border-radius: 6px;
    }}
    input:disabled {{
      color: var(--muted);
      background: #eef1ed;
    }}
    button {{
      border: 0;
      border-radius: 6px;
      padding: 10px 13px;
      background: var(--accent);
      color: white;
      cursor: pointer;
      transition: transform 120ms ease, opacity 120ms ease;
    }}
    button:hover {{ transform: translateY(-1px); }}
    button.secondary {{
      background: #e5ebe7;
      color: var(--ink);
    }}
    button.ghost {{
      width: 100%;
      margin-top: 16px;
      background: var(--ink);
      color: white;
    }}
    button.icon {{
      width: 42px;
      height: 42px;
      padding: 0;
      display: inline-grid;
      place-items: center;
      background: #e5ebe7;
      color: var(--ink);
      font-size: 20px;
      line-height: 1;
    }}
    button.danger {{
      background: var(--danger);
    }}
    .main {{
      display: grid;
      grid-template-rows: auto 1fr auto;
      height: 100svh;
      min-height: 0;
      background: var(--panel);
    }}
    .top {{
      padding: 18px 24px;
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
    .messages {{
      overflow: auto;
      min-height: 0;
      padding: 22px 24px;
      display: grid;
      align-content: start;
      gap: 14px;
    }}
    .msg {{
      width: min(78%, 820px);
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
      margin-bottom: 4px;
      text-transform: uppercase;
    }}
    .msg.user .role {{
      text-align: right;
    }}
    .bubble {{
      white-space: pre-wrap;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 11px 13px;
      background: #f8faf8;
      box-shadow: 0 2px 10px rgba(23, 32, 27, 0.04);
    }}
    .msg.assistant .bubble {{
      border-color: #cfd9d4;
      background: #ffffff;
    }}
    .msg.user .bubble {{
      border-color: #0f7b63;
      background: #0f7b63;
      color: #ffffff;
    }}
    .composer {{
      position: sticky;
      bottom: 0;
      z-index: 2;
      border-top: 1px solid var(--line);
      padding: 16px 24px;
      background: var(--panel);
    }}
    .composer-shell {{
      position: relative;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 8px;
      box-shadow: 0 8px 24px rgba(23, 32, 27, 0.06);
    }}
    .composer-row {{
      display: grid;
      grid-template-columns: auto 1fr auto;
      gap: 8px;
      align-items: end;
    }}
    .composer textarea {{
      min-height: 46px;
      max-height: 160px;
      resize: vertical;
      margin: 0;
      border: 0;
      padding: 10px 8px;
      outline: none;
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
      border-radius: 8px;
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
      border-radius: 6px;
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
      border-radius: 6px;
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
      border-radius: 6px;
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
    @keyframes rise {{
      from {{ opacity: 0; transform: translateY(4px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    @media (max-width: 920px) {{
      body {{ overflow: auto; }}
      .app {{ grid-template-columns: 1fr; }}
      .left, .right {{ border: 0; border-bottom: 1px solid var(--line); }}
      .app, .main {{ height: auto; min-height: 100svh; overflow: visible; }}
      .left, .right {{ overflow: visible; }}
      .tools {{ max-height: 40svh; }}
    }}
  </style>
</head>
<body>
  <div class="app">
    <aside class="left">
      <div class="brand">AgentBridge</div>
      <div class="subtle">Chat with an existing system through a generated kit.</div>
      <label>User</label>
      <input id="user" value="{escape_attr(config.user)}">
      <div class="field-help">Names the operator for memory and audit context.</div>
      <label>Session</label>
      <input id="session" value="{escape_attr(config.session_id)}">
      <div class="field-help">Session memory is grouped by user and session.</div>
      <label>Kit</label>
      <input id="kit" value="{escape_attr(kit)}" {"disabled" if not allow_kit_switch else ""}>
      <div class="field-help">{kit_help}</div>
      <button class="ghost" id="newChatBtn">New Chat</button>
      <label>Recent</label>
      <div class="conversation-list" id="conversations"></div>
      <div class="pending" id="pending">
        <strong>Confirmation required</strong>
        <div class="subtle" id="pendingText"></div>
        <div class="actions">
          <button id="confirmBtn">Confirm</button>
          <button class="secondary" id="cancelBtn">Cancel</button>
        </div>
      </div>
    </aside>
    <main class="main">
      <div class="top">
        <div>
          <strong>Chat</strong>
          <div class="subtle">Use /tools, /run tool key=value, confirm, or cancel.</div>
        </div>
        <div class="mode">{'Execute' if config.execute else 'Dry-run'} mode</div>
      </div>
      <div class="messages" id="messages"></div>
      <div class="composer">
        <div class="composer-shell">
          <div class="command-menu" id="commandMenu">
            <button class="suggestion" data-command="/tools"><strong>/tools</strong><span class="subtle">List tools in the active kit</span></button>
            <button class="suggestion" data-command="/run"><strong>/run</strong><span class="subtle">Run a generated tool with key=value arguments</span></button>
            <button class="suggestion" data-command="confirm"><strong>confirm</strong><span class="subtle">Approve the pending high-risk operation</span></button>
            <button class="suggestion" data-command="cancel"><strong>cancel</strong><span class="subtle">Clear the pending operation</span></button>
          </div>
          <div class="attachments" id="attachments"></div>
          <div class="composer-row">
            <input id="fileInput" type="file" multiple hidden>
            <button class="icon" id="attachBtn" title="Attach files" type="button">+</button>
            <textarea id="message" placeholder="Ask the agent to operate the system..."></textarea>
            <button id="send">Send</button>
          </div>
        </div>
      </div>
    </main>
    <aside class="right">
      <strong>Tools</strong>
      <div class="subtle">Loaded from the active kit.</div>
      <div class="tools" id="tools"></div>
    </aside>
  </div>
  <script>
    const allowKitSwitch = {allow_switch};
    const executeMode = {execute};
    const els = {{
      user: document.getElementById('user'),
      session: document.getElementById('session'),
      kit: document.getElementById('kit'),
      messages: document.getElementById('messages'),
      message: document.getElementById('message'),
      fileInput: document.getElementById('fileInput'),
      attachments: document.getElementById('attachments'),
      commandMenu: document.getElementById('commandMenu'),
      conversations: document.getElementById('conversations'),
      tools: document.getElementById('tools'),
      pending: document.getElementById('pending'),
      pendingText: document.getElementById('pendingText')
    }};
    let toolsCache = [];
    let attachments = [];
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
    function addMessage(role, text) {{
      const node = document.createElement('div');
      node.className = 'msg ' + role;
      node.innerHTML = '<div class="role">' + role + '</div><div class="bubble"></div>';
      node.querySelector('.bubble').textContent = text;
      els.messages.appendChild(node);
      els.messages.scrollTop = els.messages.scrollHeight;
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
    async function sendMessage(text) {{
      if (!text.trim() && !attachments.length) return;
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
    document.getElementById('newChatBtn').onclick = () => {{
      const now = new Date();
      const stamp = now.toISOString().replace(/[-:]/g, '').slice(0, 15);
      els.session.value = 'chat-' + stamp;
      els.messages.innerHTML = '';
      els.pending.classList.remove('show');
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
    els.message.addEventListener('input', renderCommandMenu);
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
