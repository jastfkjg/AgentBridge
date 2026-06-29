import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from io import BytesIO
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from agentbridge.chat import ChatConfig, ChatSession
from agentbridge.web import QuietThreadingHTTPServer, build_handler, normalize_target_base_url, render_index


class _FakeAgentRunner:
    def __init__(self, reply: str = "这是一个写作系统.", usage: dict[str, object] | None = None) -> None:
        self.reply = reply
        self.prompts: list[str] = []
        self.last_usage = usage or {}

    def query_text(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


class _StreamingAgentRunner:
    model = "claude-test"

    def __init__(self) -> None:
        self.last_usage = {"input_tokens": 4, "output_tokens": 6, "total_tokens": 10}

    def query_messages(self, prompt: str) -> list[object]:
        return [
            SimpleNamespace(
                content=[
                    {"type": "tool_use", "id": "tu_1", "name": "list_chapter", "input": {"project_id": "p1"}},
                    {"type": "text", "text": "Inspecting chapters."},
                ],
                usage={"input_tokens": 4, "output_tokens": 2},
            ),
            SimpleNamespace(
                content=[{"type": "tool_result", "tool_use_id": "tu_1", "content": [{"type": "text", "text": "ok"}]}],
            ),
            SimpleNamespace(result="Done.", usage={"input_tokens": 0, "output_tokens": 4}),
        ]


class _FailingAgentRunner:
    def query_messages(self, prompt: str) -> list[object]:
        exc = RuntimeError("Command failed with exit code 1 (exit code: 1) Error output: Check stderr output for details")
        exc.stderr = "Error: Session ID abc is already in use."
        raise exc


class _StreamingOnlyAgentRunner:
    model = "claude-stream"

    def __init__(self) -> None:
        self.streamed = False

    def stream_messages(self, _prompt: str):
        self.streamed = True
        yield SimpleNamespace(content=[{"type": "text", "text": "第一段"}])
        yield SimpleNamespace(content=[{"type": "text", "text": "第一段第二段"}])

    def query_messages(self, _prompt: str) -> list[object]:
        raise AssertionError("stream_process should use stream_messages before query_messages")


class _PartialStreamAgentRunner:
    model = "claude-partial"

    def stream_messages(self, _prompt: str):
        yield SimpleNamespace(event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": "你好"}})
        yield SimpleNamespace(event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": "世界"}})
        yield SimpleNamespace(content=[{"type": "text", "text": "你好世界"}])


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_kit(root: Path) -> Path:
    kit = root / "kit"
    capabilities = [
        {
            "name": "login",
            "domain": "auth",
            "resource": "session",
            "action": "create",
            "description": "Log in",
            "input_schema": {
                "type": "object",
                "properties": {"username": {"type": "string"}, "password": {"type": "string"}},
                "required": ["username", "password"],
                "additionalProperties": False,
            },
            "risk": "external_side_effect",
            "confirm_required": False,
            "source": {"kind": "openapi", "path": "openapi.json", "location": "POST /auth/login"},
            "transport": {"type": "http", "method": "POST", "path": "/auth/login"},
            "dry_run_supported": True,
        },
        {
            "name": "list_chapter",
            "domain": "writing",
            "resource": "chapter",
            "action": "list",
            "description": "List chapters",
            "input_schema": {
                "type": "object",
                "properties": {"project_id": {"type": "string"}},
                "required": ["project_id"],
                "additionalProperties": False,
            },
            "risk": "read",
            "confirm_required": False,
            "source": {"kind": "openapi", "path": "openapi.json", "location": "GET /projects/{project_id}/chapters"},
            "transport": {"type": "http", "method": "GET", "path": "/projects/{project_id}/chapters"},
            "dry_run_supported": True,
        },
        {
            "name": "delete_character",
            "domain": "writing",
            "resource": "character",
            "action": "delete",
            "description": "Delete a character",
            "input_schema": {
                "type": "object",
                "properties": {"project_id": {"type": "string"}, "character_id": {"type": "string"}},
                "required": ["project_id", "character_id"],
                "additionalProperties": False,
            },
            "risk": "destructive",
            "confirm_required": True,
            "source": {"kind": "openapi", "path": "openapi.json", "location": "DELETE /projects/{project_id}/characters/{character_id}"},
            "transport": {"type": "http", "method": "DELETE", "path": "/projects/{project_id}/characters/{character_id}"},
            "dry_run_supported": True,
        },
    ]
    _write_json(kit / "capabilities.json", capabilities)
    _write_json(
        kit / "guardrails" / "permissions.json",
        {
            "tools": {
                item["name"]: {
                    "risk": item["risk"],
                    "confirm_required": item["confirm_required"],
                    "transport": item["transport"],
                    "resource": item["resource"],
                    "action": item["action"],
                }
                for item in capabilities
            }
        },
    )
    return kit


class _FakeHTTPResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, payload: object | None = None, headers: dict[str, str] | None = None) -> None:
        self.payload = payload if payload is not None else {"ok": True}
        if headers is not None:
            self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class _ConnectivityResponse:
    status = 204
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class ChatSessionTests(unittest.TestCase):
    def test_chat_lists_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            session = ChatSession(ChatConfig(kit_dir=kit, memory_enabled=False))

            response = session.process("/tools")

            self.assertEqual(response.status, "tools")
            self.assertIn("list_chapter", response.message)
            list_chapter = next(tool for tool in response.tools if tool["name"] == "list_chapter")
            self.assertEqual(list_chapter["required"], ["project_id"])
            self.assertEqual(list_chapter["property_schemas"]["project_id"]["type"], "string")

    def test_chat_stores_pending_high_risk_tool_and_confirms(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            memory = Path(tmp) / "memory.json"
            session = ChatSession(
                ChatConfig(
                    kit_dir=kit,
                    base_url="http://example.test",
                    headers={"Authorization": "Bearer secret"},
                    memory_file=memory,
                    session_id="s1",
                )
            )

            response = session.process("/run delete_character project_id=p1 character_id=c1")

            self.assertEqual(response.status, "needs_confirmation")
            self.assertIsNotNone(response.pending)
            self.assertIn("DELETE http://example.test/projects/p1/characters/c1", response.message)
            self.assertIn("<redacted>", response.message)
            self.assertIn("Reason:", response.message)

            restored = ChatSession(ChatConfig(kit_dir=kit, memory_file=memory, session_id="s1"))
            self.assertIsNotNone(restored.pending)

            confirmed = restored.process("confirm")
            self.assertEqual(confirmed.status, "tool_result")
            self.assertFalse(confirmed.tool_result["would_execute"])

    def test_chat_executes_safe_tool_when_execute_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            session = ChatSession(ChatConfig(kit_dir=kit, base_url="http://example.test", execute=True, memory_enabled=False))

            with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse()) as urlopen:
                response = session.process("/run list_chapter project_id=p1")

            self.assertEqual(response.status, "tool_result")
            request = urlopen.call_args.args[0]
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.full_url, "http://example.test/projects/p1/chapters")

    def test_chat_persists_bearer_token_from_login_response_for_later_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            memory = Path(tmp) / "memory.json"
            session = ChatSession(ChatConfig(kit_dir=kit, base_url="http://example.test", execute=True, memory_file=memory, session_id="s1"))

            responses = [
                _FakeHTTPResponse({"access_token": "jwt-123456"}),
                _FakeHTTPResponse({"items": []}),
            ]

            with patch("urllib.request.urlopen", side_effect=responses) as urlopen:
                login = session.process("/run login username=admin password=secret")
                listed = session.process("/run list_chapter project_id=p1")

            self.assertEqual(login.status, "tool_result")
            self.assertEqual(listed.status, "tool_result")
            second_request = urlopen.call_args_list[1].args[0]
            self.assertEqual(second_request.headers.get("Authorization"), "Bearer jwt-123456")

            restored = ChatSession(ChatConfig(kit_dir=kit, base_url="http://example.test", execute=True, memory_file=memory, session_id="s1"))
            self.assertEqual(restored.config.headers.get("Authorization"), "Bearer jwt-123456")

    def test_chat_reports_read_only_policy_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            session = ChatSession(ChatConfig(kit_dir=kit, read_only=True, memory_enabled=False))

            response = session.process("/run delete_character project_id=p1 character_id=c1")

            self.assertEqual(response.status, "needs_confirmation")
            confirmed = session.process("confirm")
            self.assertIn("blocked by runtime policy", confirmed.message)

    def test_chat_runtime_switch_clears_pending_and_updates_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            session = ChatSession(ChatConfig(kit_dir=kit, memory_enabled=False))
            session.process("/run delete_character project_id=p1 character_id=c1")
            session.agent_runner = object()

            session.update_runtime(base_url="http://example.test", execute=True)

            self.assertIsNone(session.pending)
            self.assertTrue(session.config.execute)
            self.assertEqual(session.config.base_url, "http://example.test")
            self.assertTrue(session.server.config.execute)
            self.assertEqual(session.server.config.base_url, "http://example.test")
            self.assertIsNone(session.agent_runner)

    def test_chat_persists_runtime_base_url_to_the_kit(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            session = ChatSession(ChatConfig(kit_dir=kit, memory_enabled=False))

            session.update_runtime(base_url="http://localhost:3001", execute=True)

            restored = ChatSession(ChatConfig(kit_dir=kit, memory_enabled=False))
            self.assertTrue(restored.config.execute)
            self.assertEqual(restored.config.base_url, "http://localhost:3001")

    def test_chat_persists_login_credentials_to_the_kit_and_reuses_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            session = ChatSession(ChatConfig(kit_dir=kit, base_url="http://example.test", execute=True, memory_enabled=False))

            with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse({"access_token": "jwt-123456"})):
                login = session.process("/run login username=admin password=secret")

            restored = ChatSession(ChatConfig(kit_dir=kit, base_url="http://example.test", execute=True, memory_enabled=False))
            with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse({"access_token": "jwt-789"})) as urlopen:
                reused = restored.process("/run login")

            self.assertEqual(login.status, "tool_result")
            self.assertEqual(reused.status, "tool_result")
            request = urlopen.call_args.args[0]
            self.assertEqual(json.loads(request.data.decode("utf-8")), {"username": "admin", "password": "secret"})

    def test_chat_falls_back_to_agent_for_natural_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            runner = _FakeAgentRunner(
                "该系统用于管理写作项目、章节和角色。",
                {"input_tokens": 80, "output_tokens": 20, "total_tokens": 100},
            )
            session = ChatSession(ChatConfig(kit_dir=kit, memory_enabled=False, agent_runner=runner))

            response = session.process("介绍下该系统")

            self.assertEqual(response.status, "agent_response")
            self.assertEqual(response.message, "该系统用于管理写作项目、章节和角色。")
            self.assertEqual(runner.prompts, ["介绍下该系统"])
            self.assertNotIn("I could not map that to a tool", response.message)
            self.assertEqual(response.usage["total_tokens"], 100)
            self.assertEqual(response.usage["session_total_tokens"], 100)

    def test_chat_sends_follow_up_agent_turns_to_the_same_runner_without_rewriting_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            runner = _FakeAgentRunner("第一轮回答。")
            session = ChatSession(ChatConfig(kit_dir=kit, memory_enabled=False, agent_runner=runner))

            session.process("介绍下登录流程")
            runner.reply = "继续说明。"
            response = session.process("继续")

            self.assertEqual(response.status, "agent_response")
            self.assertEqual(runner.prompts[0], "介绍下登录流程")
            self.assertEqual(runner.prompts[1], "继续")

    def test_chat_explains_agent_configuration_when_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=True):
            kit = _make_kit(Path(tmp))
            session = ChatSession(ChatConfig(kit_dir=kit, memory_enabled=False))

            response = session.process("介绍下该系统")

            self.assertEqual(response.status, "agent_unavailable")
            self.assertIn("ANTHROPIC_API_KEY", response.message)
            self.assertNotIn("I could not map that to a tool", response.message)

    def test_chat_uses_agent_runner_for_custom_llm_base_url_env(self):
        class FakeAgentRunner:
            calls: list[dict[str, object]] = []

            def __init__(self, *args, **kwargs):
                FakeAgentRunner.calls.append({"args": args, "kwargs": kwargs})
                self.last_usage = {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}
                self.model = kwargs.get("model")

            def query_text(self, _prompt: str) -> str:
                return "这是 Claude Agent SDK DeepSeek response。"

        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            env = {
                "ANTHROPIC_API_KEY": "sk-test",
                "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
                "ANTHROPIC_MODEL": "deepseek-v4-flash",
            }
            with patch.dict(os.environ, env, clear=True), patch("agentbridge.agent.AgentRunner", FakeAgentRunner):
                session = ChatSession(ChatConfig(kit_dir=kit, memory_enabled=False))

                response = session.process("介绍下这个系统")

        self.assertEqual(response.status, "agent_response")
        self.assertEqual(response.message, "这是 Claude Agent SDK DeepSeek response。")
        self.assertEqual(FakeAgentRunner.calls[0]["kwargs"]["base_url"], "https://api.deepseek.com/anthropic")
        self.assertEqual(FakeAgentRunner.calls[0]["kwargs"]["model"], "deepseek-v4-flash")
        self.assertEqual(response.usage["total_tokens"], 18)

    def test_web_server_suppresses_browser_connection_reset_tracebacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            config = ChatConfig(kit_dir=kit, memory_enabled=False, agent_runner=_FakeAgentRunner("received"))
            server = QuietThreadingHTTPServer(("127.0.0.1", 0), build_handler(config))
            try:
                with patch("socketserver.BaseServer.handle_error") as handle_error:
                    try:
                        raise ConnectionResetError("reset")
                    except ConnectionResetError:
                        server.handle_error(None, ("127.0.0.1", 1))

                handle_error.assert_not_called()
            finally:
                server.server_close()

    def test_chat_stream_process_emits_tool_timeline_usage_and_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            session = ChatSession(ChatConfig(kit_dir=kit, memory_enabled=False, agent_runner=_StreamingAgentRunner()))

            events = [event.to_dict() for event in session.stream_process("summarize chapters")]

            self.assertEqual(events[0]["type"], "message_start")
            self.assertIn({"type": "tool_use", "id": "tu_1", "name": "list_chapter", "input": {"project_id": "p1"}}, events)
            self.assertTrue(any(event["type"] == "tool_result" and event["tool_use_id"] == "tu_1" for event in events))
            self.assertTrue(any(event["type"] == "usage" and event["usage"]["session_total_tokens"] == 10 for event in events))
            self.assertEqual(events[-1]["type"], "done")
            self.assertEqual(events[-1]["status"], "agent_response")

    def test_chat_stream_process_prefers_runner_stream_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            runner = _StreamingOnlyAgentRunner()
            session = ChatSession(ChatConfig(kit_dir=kit, memory_enabled=False, agent_runner=runner))

            events = [event.to_dict() for event in session.stream_process("介绍下这个系统")]

        deltas = [event["text"] for event in events if event["type"] == "assistant_text_delta"]
        self.assertTrue(runner.streamed)
        self.assertEqual(deltas, ["第一段", "第二段"])
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["message"], "第一段第二段")

    def test_chat_stream_process_emits_sdk_partial_text_deltas_before_final_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            session = ChatSession(ChatConfig(kit_dir=kit, memory_enabled=False, agent_runner=_PartialStreamAgentRunner()))

            events = [event.to_dict() for event in session.stream_process("介绍下这个系统")]

        deltas = [event["text"] for event in events if event["type"] == "assistant_text_delta"]
        self.assertEqual(deltas, ["你好", "世界"])
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["message"], "你好世界")

    def test_chat_stream_process_surfaces_agent_stderr_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            session = ChatSession(ChatConfig(kit_dir=kit, memory_enabled=False, agent_runner=_FailingAgentRunner()))

            events = [event.to_dict() for event in session.stream_process("介绍下这个系统")]

            error = next(event for event in events if event["type"] == "error")
            self.assertIn("Session ID abc is already in use", error["message"])


class WebChatTests(unittest.TestCase):
    def test_rendered_web_ui_has_command_suggestions_upload_and_conversation_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            config = ChatConfig(kit_dir=kit, memory_enabled=False, agent_runner=_FakeAgentRunner("received"))

            html = render_index(config, allow_kit_switch=False)

            self.assertIn('id="commandMenu"', html)
            self.assertIn('data-command="/tools"', html)
            self.assertIn('data-command="/run"', html)
            self.assertIn('id="fileInput"', html)
            self.assertIn('id="attachBtn"', html)
            self.assertIn('id="attachments"', html)
            self.assertIn('id="newChatBtn"', html)
            self.assertIn('id="conversations"', html)
            self.assertIn("\\n\\nAttached files:\\n", html)

    def test_rendered_web_ui_uses_polished_chat_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            config = ChatConfig(kit_dir=kit, memory_enabled=False)

            html = render_index(config, allow_kit_switch=False)

            self.assertIn("workspace-shell", html)
            self.assertIn("chat-panel", html)
            self.assertIn("message-stream", html)
            self.assertIn("composer-card", html)
            self.assertIn("composer-actions", html)
            self.assertIn("send-button", html)
            self.assertIn("max-width: 920px", html)
            self.assertIn("border-radius: 22px", html)
            self.assertIn("width: fit-content", html)
            self.assertIn("max-width: min(72%, 720px)", html)
            self.assertIn("aria-label=\"Send message\"", html)
            self.assertIn("send-icon", html)

    def test_rendered_web_ui_renders_markdown_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            config = ChatConfig(kit_dir=kit, memory_enabled=False)

            html = render_index(config, allow_kit_switch=False)

            self.assertIn('src="/assets/markdown-it.min.js"', html)
            self.assertNotIn("cdn.jsdelivr.net", html)
            self.assertNotIn("unpkg.com", html)
            self.assertIn("markdown-body", html)

    def test_rendered_web_ui_supports_safe_offline_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            config = ChatConfig(kit_dir=kit, memory_enabled=False)

            html = render_index(config, allow_kit_switch=False)

            self.assertIn("window.markdownit", html)
            self.assertIn("html: false", html)
            self.assertIn("sanitizeMarkdownFragment", html)
            self.assertIn("allowedMarkdownTags", html)
            self.assertIn("isSafeLink", html)
            self.assertIn("table-scroll", html)
            self.assertIn(".table-scroll {\n      width: 100%;", html)
            self.assertIn(".msg {\n      min-width: 0;", html)
            self.assertIn("renderPlainText", html)
            self.assertIn("role === 'user'", html)

    def test_rendered_web_ui_uses_reading_first_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            config = ChatConfig(kit_dir=kit, memory_enabled=False)

            html = render_index(config, allow_kit_switch=False)

            self.assertIn("navigation-rail", html)
            self.assertIn("context-drawer", html)
            self.assertIn("reading-column", html)
            self.assertIn('aria-label="Open navigation"', html)
            self.assertIn("drawer-panel", html)
            self.assertNotIn("tool-rail", html)
            self.assertIn("@media (max-width: 760px)", html)

    def test_rendered_web_ui_uses_sse_streaming_and_interrupt_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            config = ChatConfig(kit_dir=kit, memory_enabled=False)

            html = render_index(config, allow_kit_switch=False)

            self.assertIn("/api/chat/stream", html)
            self.assertIn("/api/chat/interrupt", html)
            self.assertIn("readStreamResponse", html)
            self.assertIn("renderTimelineEvent", html)
            self.assertIn('id="interruptBtn"', html)

    def test_handler_writes_server_sent_events(self):
        handler_cls = build_handler(ChatConfig(kit_dir=Path("/tmp/kit"), memory_enabled=False))

        class FakeHandler:
            def __init__(self) -> None:
                self.headers: list[tuple[str, str]] = []
                self.wfile = BytesIO()
                self.status = None
                self.flushes = 0

            def send_response(self, status: int) -> None:
                self.status = status

            def send_header(self, key: str, value: str) -> None:
                self.headers.append((key, value))

            def end_headers(self) -> None:
                return

        fake = FakeHandler()
        fake.wfile.flush = lambda: setattr(fake, "flushes", fake.flushes + 1)

        handler_cls._send_sse(fake, [SimpleNamespace(type="assistant_text", to_dict=lambda: {"type": "assistant_text", "text": "hi"})])

        self.assertEqual(fake.status, 200)
        self.assertIn(("Content-Type", "text/event-stream; charset=utf-8"), fake.headers)
        self.assertIn(b"event: assistant_text\n", fake.wfile.getvalue())
        self.assertIn(b'data: {"text": "hi", "type": "assistant_text"}\n\n', fake.wfile.getvalue())
        self.assertEqual(fake.flushes, 1)

    def test_rendered_web_ui_blocks_duplicate_send_while_request_is_in_flight(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            config = ChatConfig(kit_dir=kit, memory_enabled=False)

            html = render_index(config, allow_kit_switch=False)

            self.assertIn("let sendInFlight = false", html)
            self.assertIn("if (sendInFlight) return", html)
            self.assertIn("setSending(true)", html)
            self.assertIn("setSending(false)", html)
            self.assertIn("els.send.disabled = sending", html)
            self.assertIn(".send-button[hidden]", html)
            self.assertIn("display: none", html)
            self.assertIn("els.send.hidden = sending", html)
            self.assertIn("els.interrupt.hidden = !sending", html)
            self.assertLess(html.index("addMessage('user', displayTextWithAttachments(text))"), html.index("setSending(true)"))
            self.assertLess(html.index("els.message.value = ''"), html.index("setSending(true)"))
            self.assertIn("setSending(false);", html[html.index("event.type === 'error'"):html.index("event.type === 'done'")])
            done_block = html[html.index("event.type === 'done'"):html.index("if (!sawDone")]
            self.assertIn("setSending(false);", done_block)

    def test_rendered_web_ui_resolves_pending_with_buttons_without_sending_chat_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            config = ChatConfig(kit_dir=kit, memory_enabled=False)

            html = render_index(config, allow_kit_switch=False)

            self.assertIn("/api/chat/agent-permission", html)
            self.assertIn("/api/chat/pending", html)
            self.assertIn("currentPending.kind === 'agent_permission'", html)
            self.assertIn("document.getElementById('confirmBtn').onclick = () => resolvePending(true)", html)
            self.assertIn("document.getElementById('cancelBtn').onclick = () => resolvePending(false)", html)
            self.assertNotIn("document.getElementById('confirmBtn').onclick = () => sendMessage('confirm')", html)

    def test_rendered_web_ui_collapses_long_authorization_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            config = ChatConfig(kit_dir=kit, memory_enabled=False)

            html = render_index(config, allow_kit_switch=False)

            self.assertIn('id="pendingSummary"', html)
            self.assertIn('id="pendingDetails"', html)
            self.assertIn("authorization-summary", html)
            self.assertIn("authorization-command", html)
            self.assertIn("overflow-wrap: anywhere", html)
            self.assertIn("pendingDetails.open = false", html)
            self.assertIn("pendingSummary.textContent", html)

    def test_rendered_web_ui_sends_runtime_state_only_after_explicit_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            config = ChatConfig(kit_dir=kit, memory_enabled=False)

            html = render_index(config, allow_kit_switch=False)

            self.assertIn("function stateQuery(includeRuntime = false)", html)
            self.assertIn("if (includeRuntime)", html)
            self.assertIn("loadState(true)", html)
            self.assertIn("loadState(false)", html)

    def test_rendered_web_ui_aligns_user_messages_to_the_right(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            config = ChatConfig(kit_dir=kit, memory_enabled=False)

            html = render_index(config, allow_kit_switch=False)

            self.assertIn(".msg.user", html)
            self.assertIn("justify-self: end", html)
            self.assertIn("border-radius: 14px", html)

    def test_rendered_web_ui_keeps_composer_visible_and_explains_context_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            config = ChatConfig(kit_dir=kit, memory_enabled=False, user="alice", session_id="demo")

            html = render_index(config, allow_kit_switch=False)

            self.assertIn("height: 100svh", html)
            self.assertIn(".composer", html)
            self.assertIn("position: sticky", html)
            self.assertIn("bottom: 0", html)
            self.assertIn("Session memory is grouped by user and session.", html)
            self.assertIn("Start with --allow-kit-switch to edit this path.", html)
            self.assertIn('id="kit"', html)
            self.assertIn("disabled", html)

    def test_rendered_web_ui_allows_kit_switch_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            config = ChatConfig(kit_dir=kit, memory_enabled=False)

            html = render_index(config, allow_kit_switch=True)

            self.assertIn("Choose any generated Agent Integration Kit directory for this session.", html)
            self.assertNotIn('id="kit" value="' + str(kit) + '" disabled', html)

    def test_rendered_web_ui_supports_runtime_mode_switch_and_connectivity_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            config = ChatConfig(kit_dir=kit, base_url="http://localhost:8080", execute=False, memory_enabled=False)

            html = render_index(config, allow_kit_switch=False)

            self.assertIn('id="runtimeMode"', html)
            self.assertIn('data-mode="dry-run"', html)
            self.assertIn('data-mode="execute"', html)
            self.assertIn('id="baseUrl"', html)
            self.assertIn('id="testConnectionBtn"', html)
            self.assertIn('id="connectionStatus"', html)
            self.assertIn("execute: runtimeExecute", html)
            self.assertIn("base_url: runtimeExecute ? els.baseUrl.value.trim() : ''", html)
            self.assertIn("applyRuntimeMode", html)
            self.assertIn("testConnectivity", html)

    def test_rendered_web_ui_supports_clickable_tools_parameter_templates_and_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            config = ChatConfig(kit_dir=kit, memory_enabled=False)

            html = render_index(config, allow_kit_switch=False)

            self.assertIn("tool-button", html)
            self.assertIn("buildToolCommand", html)
            self.assertIn("insertToolCommand", html)
            self.assertIn("Required parameters", html)
            self.assertIn('id="usageButton"', html)
            self.assertIn('id="usagePanel"', html)
            self.assertIn("renderUsage", html)
            self.assertIn("session_total_tokens", html)
            self.assertIn("approval-card", html)
            self.assertIn('id="confirmBtn"', html)
            self.assertIn('id="cancelBtn"', html)

    def test_target_base_url_requires_http_or_https(self):
        self.assertEqual(normalize_target_base_url(" http://localhost:8080/ "), "http://localhost:8080")
        self.assertEqual(normalize_target_base_url("https://example.test/api/"), "https://example.test/api")
        with self.assertRaisesRegex(ValueError, "http:// or https://"):
            normalize_target_base_url("file:///tmp/system")
        with self.assertRaisesRegex(ValueError, "host"):
            normalize_target_base_url("http:///missing-host")

    def test_web_api_chat_and_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            config = ChatConfig(kit_dir=kit, memory_enabled=False)

            from http.server import ThreadingHTTPServer

            server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(config))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            base = f"http://127.0.0.1:{server.server_port}"

            tools = json.loads(urllib.request.urlopen(base + "/api/tools").read().decode("utf-8"))
            self.assertEqual(tools["tools"][0]["name"], "delete_character")

            body = json.dumps({"message": "/tools"}).encode("utf-8")
            req = urllib.request.Request(base + "/api/chat", data=body, headers={"Content-Type": "application/json"}, method="POST")
            response = json.loads(urllib.request.urlopen(req).read().decode("utf-8"))

            self.assertEqual(response["status"], "tools")
            self.assertIn("list_chapter", response["message"])

    def test_web_api_rejects_execute_mode_without_base_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            config = ChatConfig(kit_dir=kit, memory_enabled=False)

            from http.server import ThreadingHTTPServer

            server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(config))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            base = f"http://127.0.0.1:{server.server_port}"

            body = json.dumps({"message": "/tools", "execute": True, "base_url": ""}).encode("utf-8")
            req = urllib.request.Request(base + "/api/chat", data=body, headers={"Content-Type": "application/json"}, method="POST")

            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(req)

            self.assertEqual(raised.exception.code, 400)
            payload = json.loads(raised.exception.read().decode("utf-8"))
            self.assertIn("Base URL is required", payload["error"])

    def test_web_api_tests_target_connectivity(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            config = ChatConfig(kit_dir=kit, memory_enabled=False)

            from http.server import ThreadingHTTPServer

            server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(config))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            base = f"http://127.0.0.1:{server.server_port}"
            body = json.dumps({"base_url": "http://system.test"}).encode("utf-8")
            req = urllib.request.Request(
                base + "/api/connectivity",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with patch("agentbridge.web.url_open", return_value=_ConnectivityResponse()) as url_open:
                response = json.loads(urllib.request.urlopen(req).read().decode("utf-8"))

            self.assertTrue(response["reachable"])
            self.assertEqual(response["status"], 204)
            self.assertEqual(response["method"], "HEAD")
            target_request = url_open.call_args.args[0]
            self.assertEqual(target_request.full_url, "http://system.test")
            self.assertEqual(target_request.get_method(), "HEAD")

    def test_web_serves_local_markdown_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            config = ChatConfig(kit_dir=kit, memory_enabled=False)

            from http.server import ThreadingHTTPServer

            server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(config))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            base = f"http://127.0.0.1:{server.server_port}"

            with urllib.request.urlopen(base + "/assets/markdown-it.min.js") as response:
                script = response.read().decode("utf-8")
                content_type = response.headers.get("Content-Type")
                cache_control = response.headers.get("Cache-Control")

            self.assertIn("markdownit", script)
            self.assertEqual(content_type, "text/javascript; charset=utf-8")
            self.assertEqual(cache_control, "public, max-age=3600")

    def test_web_api_lists_recent_conversations_from_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            memory = kit / ".agentbridge-chat-memory.json"
            _write_json(
                memory,
                {
                    f"alice:first:{kit.resolve()}": {
                        "history": [{"role": "user", "content": "show chapters"}],
                        "pending": None,
                    },
                    f"alice:second:{kit.resolve()}": {
                        "history": [{"role": "assistant", "content": "create chapter planned"}],
                        "pending": {"tool": "create_chapter"},
                    },
                    f"bob:ignored:{kit.resolve()}": {"history": [], "pending": None},
                },
            )
            config = ChatConfig(kit_dir=kit, user="alice")

            from http.server import ThreadingHTTPServer

            server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(config))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            base = f"http://127.0.0.1:{server.server_port}"

            data = json.loads(urllib.request.urlopen(base + "/api/conversations?user=alice").read().decode("utf-8"))

            sessions = {item["session_id"]: item for item in data["conversations"]}
            self.assertEqual(set(sessions), {"first", "second"})
            self.assertEqual(sessions["first"]["preview"], "show chapters")
            self.assertTrue(sessions["second"]["has_pending"])

    def test_web_api_accepts_attachment_metadata_with_chat_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            config = ChatConfig(kit_dir=kit, memory_enabled=False, agent_runner=_FakeAgentRunner("received"))

            from http.server import ThreadingHTTPServer

            server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(config))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            base = f"http://127.0.0.1:{server.server_port}"

            body = json.dumps(
                {
                    "message": "review attached file",
                    "attachments": [
                        {"name": "notes.txt", "size": 12, "type": "text/plain", "content": "hello from file"}
                    ],
                }
            ).encode("utf-8")
            req = urllib.request.Request(base + "/api/chat", data=body, headers={"Content-Type": "application/json"}, method="POST")
            response = json.loads(urllib.request.urlopen(req).read().decode("utf-8"))

            self.assertEqual(response["status"], "agent_response")
            self.assertIn("notes.txt", response["history"][0]["content"])
            self.assertIn("hello from file", response["history"][0]["content"])


if __name__ == "__main__":
    unittest.main()
