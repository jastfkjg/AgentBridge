import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentbridge.audit import read_audit_events
from agentbridge.mcp_server import AgentBridgeMCPServer, MCPServerConfig
from agentbridge.kit import validate_kit


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
            "description": "Log in and capture runtime authentication headers",
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
        {
            "name": "get_project",
            "domain": "writing",
            "resource": "project",
            "action": "get",
            "description": "Fetch a project through GraphQL",
            "input_schema": {
                "type": "object",
                "properties": {"project_id": {"type": "string"}},
                "required": ["project_id"],
                "additionalProperties": False,
            },
            "risk": "read",
            "confirm_required": False,
            "source": {"kind": "graphql", "path": "schema.graphql", "location": "Query.project"},
            "transport": {
                "type": "graphql",
                "operation": "query",
                "field": "project",
                "variables": [{"name": "id", "arg": "project_id", "type": "ID!", "required": True}],
                "return_type": "Project",
            },
            "dry_run_supported": True,
        },
        {
            "name": "list_project_database",
            "domain": "writing",
            "resource": "project",
            "action": "list",
            "description": "List project rows from SQL",
            "input_schema": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "limit": {"type": "number"}},
                "required": [],
                "additionalProperties": False,
            },
            "risk": "read",
            "confirm_required": False,
            "source": {"kind": "database_schema", "path": "schema.sql", "location": "table projects"},
            "transport": {
                "type": "database",
                "operation": "list",
                "table": "projects",
                "columns": ["id", "title"],
                "read_only": True,
                "default_limit": 100,
                "max_limit": 100,
            },
            "dry_run_supported": True,
        },
    ]
    _write_json(kit / "capabilities.json", capabilities)
    _write_json(
        kit / "guardrails" / "permissions.json",
        {
            "policy": {
                "risk_actions": {
                    "read": "allow",
                    "write": "confirm",
                    "destructive": "deny",
                    "external_side_effect": "confirm",
                }
            },
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


def _call_payload(server: AgentBridgeMCPServer, name: str, arguments: dict[str, object]) -> dict[str, object]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": name,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    return json.loads(response["result"]["content"][0]["text"])


class MCPServerTests(unittest.TestCase):
    def test_tools_list_includes_confirmation_parameter_for_high_risk_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            server = AgentBridgeMCPServer(MCPServerConfig(kit_dir=kit))

            response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

            tools = {tool["name"]: tool for tool in response["result"]["tools"]}
            self.assertNotIn("confirmed", tools["delete_character"]["inputSchema"]["properties"])
            self.assertIn("confirmed", tools["create_chapter"]["inputSchema"]["properties"])
            self.assertIn("confirmed", tools["login"]["inputSchema"]["properties"])

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
            "params": {"name": "create_chapter", "arguments": {"project_id": "p1", "title": "Opening", "confirmed": True}},
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
                        "params": {"name": "create_chapter", "arguments": {"project_id": "p1", "title": "Opening", "confirmed": True}},
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

    def test_graphql_tool_preview_and_execute_uses_endpoint_and_variables(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            server = AgentBridgeMCPServer(
                MCPServerConfig(
                    kit_dir=kit,
                    graphql_endpoint="http://example.test/graphql",
                    execute=True,
                )
            )

            with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse()) as urlopen:
                response = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 40,
                        "method": "tools/call",
                        "params": {"name": "get_project", "arguments": {"project_id": "p1"}},
                    }
                )

            payload = json.loads(response["result"]["content"][0]["text"])
            request = urlopen.call_args.args[0]
            body = json.loads(request.data.decode("utf-8"))
            self.assertEqual(payload["status"], "executed")
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.full_url, "http://example.test/graphql")
            self.assertEqual(body["variables"], {"id": "p1"})
            self.assertIn("query AgentBridgeProject($id: ID!)", body["query"])

    def test_sql_read_only_tool_selects_rows_with_capped_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "app.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, title TEXT)")
                conn.executemany("INSERT INTO projects (id, title) VALUES (?, ?)", [("p1", "One"), ("p2", "Two")])
            kit = _make_kit(root)
            server = AgentBridgeMCPServer(
                MCPServerConfig(
                    kit_dir=kit,
                    database_url=f"sqlite://{db_path}",
                    execute=True,
                )
            )

            response = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 41,
                    "method": "tools/call",
                    "params": {"name": "list_project_database", "arguments": {"limit": 1000}},
                }
            )

            payload = json.loads(response["result"]["content"][0]["text"])
            self.assertEqual(payload["status"], "executed")
            self.assertEqual(payload["request"]["query"], 'SELECT "id", "title" FROM "projects" LIMIT ?')
            self.assertEqual(payload["request"]["params"], [100])
            self.assertEqual(payload["response"]["row_count"], 2)

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
                    "params": {"name": "create_chapter", "arguments": {"project_id": "p1", "title": "Opening", "confirmed": True}},
                }
            )

            payload = json.loads(response["result"]["content"][0]["text"])
            self.assertIn("Read-only mode blocks", payload["policy_error"])
            event = json.loads(audit_log.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(event["outcome"], "blocked")
            self.assertEqual(event["tool"], "create_chapter")

    def test_default_hitl_policy_requires_write_confirmation_and_denies_destructive(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            server = AgentBridgeMCPServer(MCPServerConfig(kit_dir=kit, base_url="http://example.test", execute=True))

            write_payload = _call_payload(
                server,
                "create_chapter",
                {"project_id": "p1", "title": "Opening"},
            )
            destructive_payload = _call_payload(
                server,
                "delete_character",
                {"project_id": "p1", "character_id": "c1", "confirmed": True},
            )

            self.assertFalse(write_payload["allowed"])
            self.assertTrue(write_payload["requires_confirmation"])
            self.assertEqual(write_payload["error"]["code"], "permission_denied")
            self.assertEqual(destructive_payload["error"]["code"], "permission_denied")
            self.assertIn("denied", destructive_payload["error"]["message"])

    def test_external_side_effect_requires_confirmation_even_when_tool_rule_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            server = AgentBridgeMCPServer(MCPServerConfig(kit_dir=kit, base_url="http://example.test", execute=True))

            payload = _call_payload(server, "login", {"username": "admin", "password": "secret"})

            self.assertFalse(payload["allowed"])
            self.assertTrue(payload["requires_confirmation"])
            self.assertEqual(payload["risk"], "external_side_effect")
            self.assertEqual(payload["error"]["code"], "permission_denied")

    def test_structured_schema_mismatch_error_is_returned_for_invalid_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _make_kit(Path(tmp))
            server = AgentBridgeMCPServer(MCPServerConfig(kit_dir=kit))

            payload = _call_payload(server, "list_chapter", {})

            self.assertFalse(payload["allowed"])
            self.assertEqual(payload["error"]["code"], "schema_mismatch")
            self.assertEqual(payload["error"]["category"], "validation")

    def test_audit_log_redacts_sensitive_args_and_supports_filtering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kit = _make_kit(root)
            audit_log = root / "audit.jsonl"
            server = AgentBridgeMCPServer(
                MCPServerConfig(
                    kit_dir=kit,
                    audit_log=audit_log,
                    user="alice",
                    session_id="s1",
                    model="mock-model",
                )
            )

            _call_payload(server, "login", {"username": "alice", "password": "secret"})

            events = read_audit_events(audit_log, user="alice", tool="login", risk="external_side_effect", outcome="blocked")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["user"], "alice")
            self.assertEqual(events[0]["session_id"], "s1")
            self.assertEqual(events[0]["model"], "mock-model")
            self.assertEqual(events[0]["args"]["username"], "alice")
            self.assertEqual(events[0]["args"]["password"], "<redacted>")
            self.assertIn("tool_call_id", events[0])

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
            self.assertEqual(report.summary["capability_count"], 6)


if __name__ == "__main__":
    unittest.main()
