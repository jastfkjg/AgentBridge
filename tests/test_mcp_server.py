import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentbridge.mcp_server import AgentBridgeMCPServer, MCPServerConfig
from agentbridge.kit import validate_kit


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
                "properties": {"project_id": {"type": "string"}, "page": {"type": "number"}},
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
            "name": "create_chapter",
            "domain": "writing",
            "resource": "chapter",
            "action": "create",
            "description": "Create a chapter",
            "input_schema": {
                "type": "object",
                "properties": {"project_id": {"type": "string"}, "title": {"type": "string"}},
                "required": ["project_id", "title"],
                "additionalProperties": False,
            },
            "risk": "write",
            "confirm_required": False,
            "source": {"kind": "openapi", "path": "openapi.json", "location": "POST /projects/{project_id}/chapters"},
            "transport": {"type": "http", "method": "POST", "path": "/projects/{project_id}/chapters"},
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


class MCPServerTests(unittest.TestCase):
    def test_tools_list_includes_confirmation_parameter_for_high_risk_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            server = AgentBridgeMCPServer(MCPServerConfig(kit_dir=kit))

            response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

            tools = {tool["name"]: tool for tool in response["result"]["tools"]}
            self.assertIn("confirmed", tools["delete_character"]["inputSchema"]["properties"])
            self.assertNotIn("confirmed", tools["create_chapter"]["inputSchema"]["properties"])

    def test_call_tool_returns_dry_run_plan_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            server = AgentBridgeMCPServer(
                MCPServerConfig(
                    kit_dir=kit,
                    base_url="http://example.test",
                    headers={"Authorization": "Bearer secret", "X-Tenant": "demo"},
                )
            )

            response = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "create_chapter", "arguments": {"project_id": "p1", "title": "Opening"}},
                }
            )

            payload = json.loads(response["result"]["content"][0]["text"])
            self.assertFalse(payload["would_execute"])
            self.assertEqual(payload["transport"]["method"], "POST")
            self.assertEqual(payload["request_preview"]["method"], "POST")
            self.assertEqual(payload["request_preview"]["url"], "http://example.test/projects/p1/chapters")
            self.assertEqual(payload["request_preview"]["headers"]["Authorization"], "<redacted>")
            self.assertEqual(payload["request_preview"]["headers"]["X-Tenant"], "demo")

    def test_execute_http_tool_calls_target_system(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            server = AgentBridgeMCPServer(MCPServerConfig(kit_dir=kit, base_url="http://example.test", execute=True))

            with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse()) as urlopen:
                response = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "create_chapter", "arguments": {"project_id": "p1", "title": "Opening"}},
                    }
                )

            payload = json.loads(response["result"]["content"][0]["text"])
            self.assertEqual(payload["status"], "executed")
            request = urlopen.call_args.args[0]
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.full_url, "http://example.test/projects/p1/chapters")
            self.assertEqual(json.loads(request.data.decode("utf-8")), {"title": "Opening"})

    def test_execute_get_tool_maps_remaining_args_to_query_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            server = AgentBridgeMCPServer(MCPServerConfig(kit_dir=kit, base_url="http://example.test", execute=True))

            with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse()) as urlopen:
                server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "tools/call",
                        "params": {"name": "list_chapter", "arguments": {"project_id": "p1", "page": 2}},
                    }
                )

            request = urlopen.call_args.args[0]
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.full_url, "http://example.test/projects/p1/chapters?page=2")

    def test_read_only_policy_blocks_write_tool_and_audits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kit = _make_kit(root)
            audit_log = root / "audit.jsonl"
            server = AgentBridgeMCPServer(MCPServerConfig(kit_dir=kit, execute=True, read_only=True, audit_log=audit_log))

            response = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {"name": "create_chapter", "arguments": {"project_id": "p1", "title": "Opening"}},
                }
            )

            payload = json.loads(response["result"]["content"][0]["text"])
            self.assertIn("Read-only mode blocks", payload["policy_error"])
            event = json.loads(audit_log.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(event["outcome"], "blocked")
            self.assertEqual(event["tool"], "create_chapter")

    def test_validate_kit_reports_ok_for_valid_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            _write_json(
                kit / "manifest.json",
                {
                    "protocol": "agentbridge-kit/v1",
                    "outputs": {
                        "capabilities": "capabilities.json",
                        "guardrails": "guardrails/permissions.json",
                    },
                },
            )

            report = validate_kit(kit)

            self.assertTrue(report.ok)
            self.assertEqual(report.summary["capability_count"], 3)


if __name__ == "__main__":
    unittest.main()
