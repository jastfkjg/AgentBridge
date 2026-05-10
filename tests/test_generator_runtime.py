import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agentbridge.agent import AIGenerator
from agentbridge.discovery import CapabilityDiscoverer
from agentbridge.generator import AgentKitGenerator
from agentbridge.runtime import dry_run


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "writing_system"


def _make_mock_generator() -> AIGenerator:
    gen = MagicMock(spec=AIGenerator)
    gen.api_key = "sk-test-mock-key"
    gen.base_url = ""
    gen.model = "mock-model"
    gen._backend = "anthropic"

    discoverer = CapabilityDiscoverer()
    raw_caps = discoverer.discover([EXAMPLE])

    from agentbridge.policy import risk_reason
    from agentbridge.models import SourceRef

    tool_enhancements = {}
    risk_assessments = {}
    for cap in raw_caps:
        tool_enhancements[cap.name] = {
            "description": cap.description,
            "when_to_use": f"When you need to {cap.action} {cap.resource}",
            "caveats": "",
        }
        risk_assessments[cap.name] = {
            "risk": cap.risk,
            "reason": risk_reason(cap.risk),
            "reversible": cap.risk != "destructive",
            "blast_radius": "single",
        }

    domains = sorted({cap.domain for cap in raw_caps})
    skills = {d: f"# {d.title()} Skill\n\nMock skill for testing." for d in domains}

    gen.generate_all.return_value = {
        "enhanced_capabilities": raw_caps,
        "system_prompt": "# Mock System Prompt\n\nFor testing only.",
        "skills": skills,
    }

    return gen


class GeneratorRuntimeTests(unittest.TestCase):
    def test_generates_complete_kit(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            gen = _make_mock_generator()
            kit = AgentKitGenerator(ai_generator=gen).generate([EXAMPLE], output, name="writing-kit")

            self.assertGreaterEqual(len(kit.capabilities), 8)
            self.assertTrue((output / "manifest.json").exists())
            self.assertTrue((output / "tools" / "mcp_tools.json").exists())
            self.assertTrue((output / "prompts" / "system.md").exists())
            self.assertTrue((output / "guardrails" / "permissions.json").exists())
            self.assertTrue((output / "tests" / "test_generated_tools.py").exists())

    def test_dry_run_blocks_unconfirmed_destructive_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            gen = _make_mock_generator()
            AgentKitGenerator(ai_generator=gen).generate([EXAMPLE], output, name="writing-kit")

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
            gen = _make_mock_generator()
            AgentKitGenerator(ai_generator=gen).generate([EXAMPLE], output, name="writing-kit")

            result = dry_run(
                output,
                "delete_character",
                {"project_id": "p1", "character_id": "c1"},
                confirmed=True,
            )

            self.assertTrue(result["allowed"])
            self.assertEqual(result["would_execute"], False)

    def test_generator_requires_ai_generator(self):
        with self.assertRaises(TypeError):
            AgentKitGenerator()

    def test_generate_calls_ai_generator(self):
        gen = _make_mock_generator()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            AgentKitGenerator(ai_generator=gen).generate([EXAMPLE], output, name="writing-kit")
            gen.generate_all.assert_called_once()


if __name__ == "__main__":
    unittest.main()
