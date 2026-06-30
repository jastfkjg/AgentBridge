import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from agentbridge import cli
from agentbridge.diff import diff_kits, format_diff
from agentbridge.kit import migrate_kit, validate_kit


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _capability(name: str, risk: str = "read", path: str = "/items") -> dict[str, object]:
    return {
        "name": name,
        "domain": "demo",
        "resource": name.split("_")[-1],
        "action": name.split("_")[0],
        "description": name,
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "risk": risk,
        "confirm_required": risk in {"destructive", "external_side_effect"},
        "source": {"kind": "openapi", "path": "openapi.json", "location": f"GET {path}"},
        "transport": {"type": "http", "method": "GET", "path": path},
        "dry_run_supported": True,
    }


def _write_kit(root: Path, capabilities: list[dict[str, object]]) -> Path:
    kit = root / "kit"
    _write_json(kit / "manifest.json", {"protocol": "agentbridge-kit/v1", "name": "kit", "outputs": {"capabilities": "capabilities.json", "guardrails": "guardrails/permissions.json"}})
    _write_json(kit / "capabilities.json", capabilities)
    _write_json(
        kit / "guardrails" / "permissions.json",
        {
            "tools": {
                str(item["name"]): {
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


class KitDiffTests(unittest.TestCase):
    def test_diff_reports_added_removed_changed_and_risk_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = _write_kit(root / "old", [_capability("list_item"), _capability("delete_item", "destructive", "/items/{id}")])
            new = _write_kit(root / "new", [_capability("list_item", "write"), _capability("publish_item", "external_side_effect", "/items/publish")])

            diff = diff_kits(old, new)

            self.assertEqual(diff["added"], ["publish_item"])
            self.assertEqual(diff["removed"], ["delete_item"])
            self.assertEqual(diff["risk_changed"], [{"name": "list_item", "from": "read", "to": "write"}])
            self.assertTrue(diff["changed"])
            self.assertIn("publish_item", format_diff(diff))

    def test_cli_diff_returns_nonzero_for_changed_kits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = _write_kit(root / "old", [_capability("list_item")])
            new = _write_kit(root / "new", [_capability("list_item", "write")])

            with patch("sys.stdout", new=StringIO()) as stdout:
                result = cli.main(["diff", str(old), str(new)])

            self.assertEqual(result, 2)
            self.assertIn("risk changed", stdout.getvalue())

    def test_generate_check_returns_nonzero_when_existing_kit_is_stale(self):
        spec = {
            "openapi": "3.0.0",
            "paths": {
                "/items": {
                    "get": {"operationId": "listItems"}
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            openapi = root / "openapi.json"
            openapi.write_text(json.dumps(spec), encoding="utf-8")
            kit = _write_kit(root / "existing", [])

            with patch("sys.stdout", new=StringIO()) as stdout:
                result = cli.main(["generate", str(openapi), "--output", str(kit), "--no-ai", "--check"])

            self.assertEqual(result, 2)
            self.assertIn("added", stdout.getvalue())

    def test_migrate_kit_adds_policy_and_analysis_report_without_protocol_bump(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = _write_kit(Path(tmp), [_capability("list_item")])

            result = migrate_kit(kit)
            report = validate_kit(kit)
            manifest = json.loads((kit / "manifest.json").read_text(encoding="utf-8"))
            guardrails = json.loads((kit / "guardrails" / "permissions.json").read_text(encoding="utf-8"))

            self.assertTrue(result["changed"])
            self.assertEqual(manifest["protocol"], "agentbridge-kit/v1")
            self.assertIn("analysis_report", manifest["outputs"])
            self.assertIn("policy", guardrails)
            self.assertTrue((kit / "analysis" / "report.md").exists())
            self.assertTrue(report.ok)


if __name__ == "__main__":
    unittest.main()
