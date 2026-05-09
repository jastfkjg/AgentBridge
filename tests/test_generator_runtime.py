import tempfile
import unittest
from pathlib import Path

from agentbridge.generator import AgentKitGenerator
from agentbridge.runtime import dry_run


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "writing_system"


class GeneratorRuntimeTests(unittest.TestCase):
    def test_generates_complete_kit(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            kit = AgentKitGenerator().generate([EXAMPLE], output, name="writing-kit")

            self.assertGreaterEqual(len(kit.capabilities), 8)
            self.assertTrue((output / "manifest.json").exists())
            self.assertTrue((output / "tools" / "mcp_tools.json").exists())
            self.assertTrue((output / "skills" / "writing.md").exists())
            self.assertTrue((output / "guardrails" / "permissions.json").exists())
            self.assertTrue((output / "tests" / "test_generated_tools.py").exists())

    def test_dry_run_blocks_unconfirmed_destructive_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            AgentKitGenerator().generate([EXAMPLE], output, name="writing-kit")

            result = dry_run(
                output,
                "delete_character",
                {"project_id": "p1", "character_id": "c1"},
                confirmed=False,
            )

            self.assertFalse(result["allowed"])
            self.assertTrue(result["requires_confirmation"])

    def test_dry_run_allows_confirmed_destructive_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            AgentKitGenerator().generate([EXAMPLE], output, name="writing-kit")

            result = dry_run(
                output,
                "delete_character",
                {"project_id": "p1", "character_id": "c1"},
                confirmed=True,
            )

            self.assertTrue(result["allowed"])
            self.assertEqual(result["would_execute"], False)


if __name__ == "__main__":
    unittest.main()

