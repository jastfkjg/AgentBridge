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
            if parsed.path == "/api/state":
                session = self._session_from_query(parsed.query)
                self._send_json(
                    {
                        "history": session.history[-session.config.max_history :],
                        "pending": session.pending.to_dict() if session.pending else None,
                        "tools": session.tool_summaries(),
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
                    message = str(body.get("message", ""))
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


def render_index(config: ChatConfig, allow_kit_switch: bool) -> str:
    kit = str(config.kit_dir)
    execute = "true" if config.execute else "false"
    allow_switch = "true" if allow_kit_switch else "false"
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
    body {{
      margin: 0;
      font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--surface);
      letter-spacing: 0;
    }}
    .app {{
      min-height: 100svh;
      display: grid;
      grid-template-columns: minmax(240px, 320px) 1fr minmax(260px, 360px);
    }}
    aside, main {{
      min-width: 0;
    }}
    .left, .right {{
      padding: 22px;
      border-right: 1px solid var(--line);
      background: #fbfcfa;
    }}
    .right {{
      border-right: 0;
      border-left: 1px solid var(--line);
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
    button.danger {{
      background: var(--danger);
    }}
    .main {{
      display: grid;
      grid-template-rows: auto 1fr auto;
      min-height: 100svh;
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
      padding: 22px 24px;
    }}
    .msg {{
      max-width: 880px;
      margin-bottom: 18px;
      animation: rise 160ms ease-out;
    }}
    .role {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 4px;
      text-transform: uppercase;
    }}
    .bubble {{
      white-space: pre-wrap;
      border-left: 3px solid var(--line);
      padding-left: 13px;
    }}
    .msg.assistant .bubble {{
      border-color: var(--accent);
    }}
    .composer {{
      border-top: 1px solid var(--line);
      padding: 16px 24px;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
    }}
    .composer textarea {{
      min-height: 46px;
      max-height: 160px;
      resize: vertical;
      margin: 0;
    }}
    .tools {{
      margin-top: 18px;
      display: grid;
      gap: 10px;
    }}
    .tool {{
      border-top: 1px solid var(--line);
      padding-top: 10px;
    }}
    .tool strong {{
      display: block;
      font-size: 13px;
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
      .app {{ grid-template-columns: 1fr; }}
      .left, .right {{ border: 0; border-bottom: 1px solid var(--line); }}
      .main {{ min-height: 70svh; }}
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
      <label>Session</label>
      <input id="session" value="{escape_attr(config.session_id)}">
      <label>Kit</label>
      <input id="kit" value="{escape_attr(kit)}" {"disabled" if not allow_kit_switch else ""}>
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
        <textarea id="message" placeholder="Ask the agent to operate the system..."></textarea>
        <button id="send">Send</button>
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
      tools: document.getElementById('tools'),
      pending: document.getElementById('pending'),
      pendingText: document.getElementById('pendingText')
    }};
    function payload(extra = {{}}) {{
      return Object.assign({{
        user: els.user.value,
        session_id: els.session.value,
        kit_dir: allowKitSwitch ? els.kit.value : undefined
      }}, extra);
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
    async function sendMessage(text) {{
      if (!text.trim()) return;
      addMessage('user', text);
      els.message.value = '';
      const data = await post('/api/chat', payload({{ message: text }}));
      if (data.error) {{
        addMessage('assistant', data.error);
        return;
      }}
      addMessage('assistant', data.message);
      renderPending(data.pending);
      if (data.tools && data.tools.length) renderTools(data.tools);
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
      els.tools.innerHTML = '';
      tools.forEach(tool => {{
        const node = document.createElement('div');
        node.className = 'tool';
        node.innerHTML = '<strong></strong><div class="subtle"></div>';
        node.querySelector('strong').textContent = tool.name + ' [' + tool.risk + ']';
        node.querySelector('.subtle').textContent = 'Required: ' + ((tool.required || []).join(', ') || 'none');
        els.tools.appendChild(node);
      }});
    }}
    async function loadState() {{
      const qs = new URLSearchParams({{ user: els.user.value, session: els.session.value }});
      if (allowKitSwitch) qs.set('kit', els.kit.value);
      const data = await fetch('/api/state?' + qs.toString()).then(r => r.json());
      els.messages.innerHTML = '';
      (data.history || []).forEach(item => addMessage(item.role, item.content));
      renderPending(data.pending);
      renderTools(data.tools || []);
    }}
    document.getElementById('send').onclick = () => sendMessage(els.message.value);
    els.message.addEventListener('keydown', (event) => {{
      if (event.key === 'Enter' && !event.shiftKey) {{
        event.preventDefault();
        sendMessage(els.message.value);
      }}
    }});
    document.getElementById('confirmBtn').onclick = () => sendMessage('confirm');
    document.getElementById('cancelBtn').onclick = () => sendMessage('cancel');
    [els.user, els.session, els.kit].forEach(el => el.addEventListener('change', loadState));
    loadState();
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
