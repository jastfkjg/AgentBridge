import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from agentbridge.chat import ChatConfig, ChatSession
from agentbridge.web import build_handler


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


class ChatSessionTests(unittest.TestCase):
    def test_chat_lists_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            session = ChatSession(ChatConfig(kit_dir=kit, memory_enabled=False))

            response = session.process("/tools")

            self.assertEqual(response.status, "tools")
            self.assertIn("list_chapter", response.message)

    def test_chat_stores_pending_high_risk_tool_and_confirms(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            memory = Path(tmp) / "memory.json"
            session = ChatSession(ChatConfig(kit_dir=kit, memory_file=memory, session_id="s1"))

            response = session.process("/run delete_character project_id=p1 character_id=c1")

            self.assertEqual(response.status, "needs_confirmation")
            self.assertIsNotNone(response.pending)

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


class WebChatTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
