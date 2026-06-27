import unittest
import json
import tempfile
from pathlib import Path

from agentbridge.discovery import CapabilityDiscoverer, dedupe_capabilities
from agentbridge.models import Capability, SourceRef
from agentbridge.runtime import dry_run, load_capabilities


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "writing_system"


class DiscoveryTests(unittest.TestCase):
    def test_discovers_openapi_graphql_sql_and_routes(self):
        capabilities = CapabilityDiscoverer().discover([EXAMPLE])
        names = {cap.name for cap in capabilities}

        self.assertIn("create_chapter", names)
        self.assertIn("delete_character", names)
        self.assertIn("publish_project", names)
        self.assertIn("send_email", names)
        self.assertIn("create_scene", names)

    def test_high_risk_capabilities_require_confirmation(self):
        capabilities = CapabilityDiscoverer().discover([EXAMPLE])
        high_risk = [cap for cap in capabilities if cap.risk in {"destructive", "external_side_effect"}]

        self.assertTrue(high_risk)
        self.assertTrue(all(cap.confirm_required for cap in high_risk))

    def test_dedupes_same_http_operation_from_multiple_sources(self):
        openapi = Capability(
            name="update_character",
            domain="writing",
            resource="character",
            action="update",
            description="Update character",
            input_schema={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            risk="write",
            confirm_required=False,
            source=SourceRef("openapi", "openapi.json", "PATCH /characters/{id}"),
            transport={"type": "http", "method": "PATCH", "path": "/characters/{id}", "operation_id": "updateCharacter"},
        )
        route = Capability(
            name="update_character",
            domain="writing",
            resource="character",
            action="update",
            description="PATCH route",
            input_schema={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            risk="write",
            confirm_required=False,
            source=SourceRef("source_route", "routes.py", "PATCH /characters/{id}"),
            transport={"type": "http", "method": "PATCH", "path": "/characters/{id}", "handler": "update_character"},
        )

        result = dedupe_capabilities([route, openapi])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "update_character")
        self.assertEqual(result[0].source.kind, "openapi")

    def test_disambiguates_distinct_same_named_operations_without_numeric_suffix(self):
        by_id = Capability(
            name="update_character",
            domain="writing",
            resource="character",
            action="update",
            description="Update character",
            input_schema={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            risk="write",
            confirm_required=False,
            source=SourceRef("openapi", "openapi.json", "PATCH /characters/{id}"),
            transport={"type": "http", "method": "PATCH", "path": "/characters/{id}", "operation_id": "updateCharacter"},
        )
        image = Capability(
            name="update_character",
            domain="writing",
            resource="character",
            action="update",
            description="Update character image",
            input_schema={"type": "object", "properties": {"character_id": {"type": "string"}}, "required": ["character_id"]},
            risk="write",
            confirm_required=False,
            source=SourceRef("openapi", "openapi.json", "PATCH /characters/{character_id}/image"),
            transport={"type": "http", "method": "PATCH", "path": "/characters/{character_id}/image", "operation_id": "updateCharacterImage"},
        )

        result = dedupe_capabilities([by_id, image])

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].name, "update_character")
        self.assertEqual(result[1].name, "update_character_image")
        self.assertFalse(any(cap.name.endswith("_2") for cap in result))

    def test_runtime_normalizes_legacy_numeric_tool_names_and_guardrails(self):
        capabilities = [
            {
                "name": "update_character",
                "domain": "writing",
                "resource": "character",
                "action": "update",
                "description": "Update character",
                "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
                "risk": "write",
                "confirm_required": False,
                "source": {"kind": "openapi", "path": "openapi.json", "location": "PATCH /characters/{id}"},
                "transport": {"type": "http", "method": "PATCH", "path": "/characters/{id}", "operation_id": "updateCharacter"},
                "dry_run_supported": True,
            },
            {
                "name": "update_character_2",
                "domain": "writing",
                "resource": "character",
                "action": "update",
                "description": "Duplicate route",
                "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
                "risk": "write",
                "confirm_required": False,
                "source": {"kind": "openapi", "path": "duplicate.json", "location": "PATCH /characters/{id}"},
                "transport": {"type": "http", "method": "PATCH", "path": "/characters/{id}", "operation_id": "updateCharacter"},
                "dry_run_supported": True,
            },
            {
                "name": "update_character_3",
                "domain": "writing",
                "resource": "character",
                "action": "update",
                "description": "Update character image",
                "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
                "risk": "write",
                "confirm_required": True,
                "source": {"kind": "openapi", "path": "openapi.json", "location": "PATCH /characters/{id}/image"},
                "transport": {"type": "http", "method": "PATCH", "path": "/characters/{id}/image", "operation_id": "updateCharacterImage"},
                "dry_run_supported": True,
            },
            {
                "name": "update_character_4",
                "domain": "writing",
                "resource": "character",
                "action": "update",
                "description": "DO NOT USE - scanner-generated duplicate. Use update_character instead.",
                "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
                "risk": "write",
                "confirm_required": False,
                "source": {"kind": "openapi", "path": "legacy.json", "location": "PUT /legacy/character-image/{id}"},
                "transport": {"type": "http", "method": "PUT", "path": "/legacy/character-image/{id}", "operation_id": "legacyCharacterImage"},
                "dry_run_supported": True,
            },
        ]
        rules = {
            item["name"]: {
                "risk": item["risk"],
                "confirm_required": item["confirm_required"],
                "transport": item["transport"],
            }
            for item in capabilities
        }

        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp)
            (kit / "guardrails").mkdir()
            (kit / "capabilities.json").write_text(json.dumps(capabilities), encoding="utf-8")
            (kit / "guardrails" / "permissions.json").write_text(
                json.dumps({"tools": rules}),
                encoding="utf-8",
            )

            loaded = load_capabilities(kit)
            plan = dry_run(kit, "update_character_image", {"id": "c1"})

        self.assertEqual(set(loaded), {"update_character", "update_character_image"})
        self.assertTrue(plan["requires_confirmation"])
        self.assertEqual(plan["transport"]["path"], "/characters/{id}/image")

    def test_database_name_disambiguation_ignores_empty_transport_hints(self):
        capability = Capability(
            name="create_character",
            domain="writing",
            resource="character",
            action="create",
            description="Create database character",
            input_schema={"type": "object", "properties": {}, "required": []},
            risk="write",
            confirm_required=False,
            source=SourceRef("database_schema", "schema.sql", "table Character"),
            transport={"type": "database", "table": "Character"},
        )

        result = dedupe_capabilities(
            [
                Capability(
                    name="create_character",
                    domain="writing",
                    resource="character",
                    action="create",
                    description="Create HTTP character",
                    input_schema={"type": "object", "properties": {}, "required": []},
                    risk="write",
                    confirm_required=False,
                    source=SourceRef("openapi", "openapi.json", "POST /characters"),
                    transport={"type": "http", "method": "POST", "path": "/characters"},
                ),
                capability,
            ]
        )

        self.assertEqual([item.name for item in result], ["create_character", "create_character_database"])

    def test_openapi_records_auth_and_parameter_locations(self):
        spec = {
            "security": [{"BearerAuth": []}],
            "components": {
                "securitySchemes": {
                    "BearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
                    "ApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
                }
            },
            "paths": {
                "/projects/{projectId}": {
                    "parameters": [{"name": "projectId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "get": {
                        "operationId": "getProject",
                        "security": [{"ApiKey": []}],
                        "parameters": [
                            {"name": "includeDrafts", "in": "query", "schema": {"type": "boolean"}},
                            {"name": "X-Tenant", "in": "header", "schema": {"type": "string"}},
                        ],
                    },
                }
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "openapi.json"
            path.write_text(json.dumps(spec), encoding="utf-8")
            capabilities = CapabilityDiscoverer().discover([path])

        cap = capabilities[0]
        self.assertEqual(cap.input_schema["properties"]["project_id"]["type"], "string")
        self.assertIn("project_id", cap.transport["parameters"]["path"])
        self.assertIn("include_drafts", cap.transport["parameters"]["query"])
        self.assertEqual(cap.transport["parameters"]["header"], {"x_tenant": "X-Tenant"})
        self.assertEqual(cap.transport["auth"][0]["runtime_header"], "X-API-Key")

    def test_graphql_variables_and_return_type_are_recorded(self):
        schema = """
        type Query {
          project(id: ID!, includeDrafts: Boolean): Project
        }
        type Mutation {
          publishProject(id: ID!): PublishPayload
        }
        """

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schema.graphql"
            path.write_text(schema, encoding="utf-8")
            capabilities = CapabilityDiscoverer().discover([path])

        by_field = {cap.transport["field"]: cap for cap in capabilities}
        project = by_field["project"]
        self.assertEqual(project.transport["operation"], "query")
        self.assertEqual(project.transport["return_type"], "Project")
        self.assertEqual(project.transport["variables"][0], {"name": "id", "arg": "id", "type": "ID!", "required": True})
        self.assertEqual(project.input_schema["properties"]["include_drafts"]["type"], "boolean")
        self.assertEqual(by_field["publishProject"].transport["operation"], "mutation")
        self.assertTrue(by_field["publishProject"].confirm_required)

    def test_sql_discovery_generates_read_only_list_tools_only(self):
        schema = """
        CREATE TABLE projects (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL
        );
        """

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schema.sql"
            path.write_text(schema, encoding="utf-8")
            capabilities = CapabilityDiscoverer().discover([path])

        self.assertEqual([cap.name for cap in capabilities], ["list_project"])
        cap = capabilities[0]
        self.assertEqual(cap.risk, "read")
        self.assertFalse(cap.confirm_required)
        self.assertTrue(cap.transport["read_only"])
        self.assertEqual(cap.transport["columns"], ["id", "title"])

    def test_grpc_proto_discovery_generates_method_capability(self):
        proto = """
        syntax = "proto3";
        service ProjectService {
          rpc GetProject (GetProjectRequest) returns (ProjectReply);
        }
        message GetProjectRequest {
          string project_id = 1;
          bool include_drafts = 2;
        }
        message ProjectReply {
          string title = 1;
        }
        """

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.proto"
            path.write_text(proto, encoding="utf-8")
            capabilities = CapabilityDiscoverer().discover([path])

        self.assertEqual(len(capabilities), 1)
        cap = capabilities[0]
        self.assertEqual(cap.transport["type"], "grpc")
        self.assertEqual(cap.transport["service"], "ProjectService")
        self.assertEqual(cap.transport["method"], "GetProject")
        self.assertEqual(cap.input_schema["properties"]["project_id"]["type"], "string")
        self.assertEqual(cap.input_schema["properties"]["include_drafts"]["type"], "boolean")

    def test_custom_python_plugin_discovery_uses_explicit_plugin_marker(self):
        plugin = """
AGENTBRIDGE_PLUGIN = True

def agentbridge_discover():
    return [
        {
            "name": "sync_index",
            "domain": "search",
            "resource": "index",
            "action": "sync",
            "description": "Synchronize a search index through a custom plugin.",
            "input_schema": {
                "type": "object",
                "properties": {"force": {"type": "boolean"}},
                "required": [],
                "additionalProperties": False,
            },
            "risk": "external_side_effect",
            "confirm_required": True,
            "source": {"kind": "custom_python_plugin", "path": "plugin.py", "location": "agentbridge_discover"},
            "transport": {"type": "python_plugin"},
            "dry_run_supported": True,
        }
    ]
"""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plugin.py"
            path.write_text(plugin, encoding="utf-8")
            capabilities = CapabilityDiscoverer().discover([path])

        self.assertEqual(len(capabilities), 1)
        cap = capabilities[0]
        self.assertEqual(cap.name, "sync_index")
        self.assertEqual(cap.source.kind, "custom_python_plugin")
        self.assertEqual(cap.transport["type"], "python_plugin")
        self.assertEqual(cap.transport["module"], str(path))


if __name__ == "__main__":
    unittest.main()
