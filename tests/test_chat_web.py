import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from agentbridge.chat import ChatConfig, ChatSession
from agentbridge.web import build_handler, normalize_target_base_url, render_index


class _FakeAgentRunner:
    def __init__(self, reply: str = "这是一个写作系统.", usage: dict[str, object] | None = None) -> None:
        self.reply = reply
        self.prompts: list[str] = []
        self.last_usage = usage or {}

    def query_text(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_kit(root: Path) -> Path:
    kit = root / "kit"
    capabilities = [
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

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return b'{"ok": true}'


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
