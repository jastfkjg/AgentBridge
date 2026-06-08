import tempfile
import unittest
import time
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
import json

from agentbridge import cli
from agentbridge.agent import AIGenerator
from agentbridge.discovery import CapabilityDiscoverer
from agentbridge.generator import AgentKitGenerator, GenerationBoundaryError, validate_output_boundary
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
        "rule_signals": {
            "candidate_capabilities": [cap.to_dict() for cap in raw_caps],
            "risk_policy": {},
        },
        "agent_analysis": {
            "summary": "Mock writing system analysis.",
            "business_objects": [{"name": "chapter", "description": "Story chapter", "evidence": ["openapi"]}],
            "workflows": [{"name": "write chapter", "steps": ["create", "rewrite"], "tools": ["create_chapter"], "risks": []}],
            "permission_boundaries": [],
            "side_effects": [],
            "assumptions": [],
        },
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
            self.assertTrue((output / "analysis" / "rule_signals.json").exists())
            self.assertTrue((output / "analysis" / "agent_analysis.json").exists())
            self.assertTrue((output / "spec" / "kit-protocol.md").exists())
            self.assertTrue((output / "prompts" / "system.md").exists())
            self.assertTrue((output / "guardrails" / "permissions.json").exists())
            self.assertTrue((output / "clients" / "mcp-client-configs.json").exists())
            self.assertTrue((output / "clients" / "README.md").exists())
            self.assertTrue((output / "tests" / "test_generated_tools.py").exists())
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["protocol"], "agentbridge-kit/v1")
            self.assertEqual(manifest["outputs"]["ai_analysis"], "analysis/agent_analysis.json")
            self.assertEqual(manifest["outputs"]["client_configs"], "clients/mcp-client-configs.json")

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

    def test_generate_leaves_status_when_ai_generation_fails(self):
        gen = _make_mock_generator()
        gen.generate_all.side_effect = RuntimeError("mock llm timeout")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"

            with self.assertRaises(RuntimeError):
                AgentKitGenerator(ai_generator=gen).generate([EXAMPLE], output, name="writing-kit")

            status = json.loads((output / "generation_status.json").read_text(encoding="utf-8"))
            rule_signals = json.loads((output / "analysis" / "rule_signals.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "failed")
            self.assertIn("mock llm timeout", status["message"])
            self.assertGreater(len(rule_signals["candidate_capabilities"]), 0)

    def test_generate_can_skip_ai_after_discovery_review(self):
        gen = _make_mock_generator()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            kit = AgentKitGenerator(
                ai_generator=gen,
                confirm_ai_analysis=lambda _caps, _name, _output: False,
            ).generate([EXAMPLE], output, name="writing-kit")

            gen.generate_all.assert_not_called()
            self.assertGreaterEqual(len(kit.capabilities), 8)
            self.assertTrue((output / "manifest.json").exists())
            status = json.loads((output / "generation_status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "complete")

    def test_generate_emits_waiting_heartbeat_during_ai_analysis(self):
        gen = _make_mock_generator()

        def _slow_generate_all(*_args, **_kwargs):
            time.sleep(0.05)
            return gen.generate_all.return_value

        gen.generate_all.side_effect = _slow_generate_all
        messages: list[str] = []

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            AgentKitGenerator(
                ai_generator=gen,
                progress=messages.append,
                progress_interval=0.01,
            ).generate([EXAMPLE], output, name="writing-kit")

        self.assertTrue(any("Still waiting for AI batch" in message for message in messages))

    def test_generate_reports_written_files(self):
        gen = _make_mock_generator()
        messages: list[str] = []

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            AgentKitGenerator(
                ai_generator=gen,
                progress=messages.append,
            ).generate([EXAMPLE], output, name="writing-kit")

        self.assertTrue(any("Writing kit file:" in message for message in messages))
        self.assertTrue(any(str(output / "manifest.json") in message for message in messages))

    def test_generate_enhances_all_capabilities_in_batches(self):
        gen = _make_mock_generator()
        captured_batches: list[list[Any]] = []
        captured_input_paths: list[list[Path]] = []

        def _generate_all(caps, _kit_name, input_paths=None):
            captured_batches.append(list(caps))
            captured_input_paths.append(list(input_paths or []))
            return {
                "enhanced_capabilities": list(caps),
                "rule_signals": {
                    "candidate_capabilities": [cap.to_dict() for cap in caps],
                    "risk_policy": {},
                },
                "agent_analysis": {
                    "summary": "Focused mock analysis.",
                    "business_objects": [],
                    "workflows": [],
                    "permission_boundaries": [],
                    "side_effects": [],
                    "assumptions": [],
                },
                "system_prompt": "# Focused Mock",
                "skills": {},
            }

        gen.generate_all.side_effect = _generate_all
        messages: list[str] = []

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            AgentKitGenerator(
                ai_generator=gen,
                progress=messages.append,
                analysis_capability_limit=5,
            ).generate([EXAMPLE], output, name="writing-kit")

            capabilities = json.loads((output / "capabilities.json").read_text(encoding="utf-8"))
            rule_signals = json.loads((output / "analysis" / "rule_signals.json").read_text(encoding="utf-8"))
            resume_state = json.loads((output / "analysis" / "resume_state.json").read_text(encoding="utf-8"))

        self.assertGreater(len(captured_batches), 1)
        self.assertTrue(all(len(batch) <= 5 for batch in captured_batches))
        self.assertEqual(sum(len(batch) for batch in captured_batches), len(capabilities))
        self.assertTrue(captured_input_paths)
        self.assertTrue(all(path.is_file() for paths in captured_input_paths for path in paths))
        self.assertGreater(len(capabilities), 5)
        self.assertIn("batch_enhancement", rule_signals)
        self.assertEqual(rule_signals["batch_enhancement"]["batch_size"], 5)
        self.assertEqual(resume_state["status"], "complete")
        self.assertEqual(resume_state["remaining_batch_count"], 0)
        self.assertTrue(any("Enhancing AI batch" in message for message in messages))

    def test_generate_resume_skips_completed_batches(self):
        def _batch_response(caps):
            return {
                "enhanced_capabilities": list(caps),
                "rule_signals": {
                    "candidate_capabilities": [cap.to_dict() for cap in caps],
                    "risk_policy": {},
                },
                "agent_analysis": {
                    "summary": "Batch resume mock.",
                    "business_objects": [],
                    "workflows": [],
                    "permission_boundaries": [],
                    "side_effects": [],
                    "assumptions": [],
                },
                "system_prompt": "# Batch Resume Mock",
                "skills": {},
            }

        first_gen = _make_mock_generator()
        first_calls: list[list[Any]] = []

        def _first_run(caps, _kit_name, input_paths=None):
            first_calls.append(list(caps))
            if len(first_calls) == 1:
                return _batch_response(caps)
            raise RuntimeError("interrupt after first batch")

        first_gen.generate_all.side_effect = _first_run

        second_gen = _make_mock_generator()
        second_calls: list[list[Any]] = []

        def _second_run(caps, _kit_name, input_paths=None):
            second_calls.append(list(caps))
            return _batch_response(caps)

        second_gen.generate_all.side_effect = _second_run

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            with self.assertRaises(RuntimeError):
                AgentKitGenerator(
                    ai_generator=first_gen,
                    progress=lambda _message: None,
                    analysis_capability_limit=5,
                ).generate([EXAMPLE], output, name="writing-kit")

            state = json.loads((output / "analysis" / "resume_state.json").read_text(encoding="utf-8"))
            self.assertGreater(state["analysis_batch_count"], 1)
            self.assertTrue((output / "analysis" / "batches" / "batch_0001.json").exists())

            AgentKitGenerator(
                ai_generator=second_gen,
                progress=lambda _message: None,
                analysis_capability_limit=5,
                resume=True,
            ).generate([EXAMPLE], output, name="writing-kit")

        self.assertGreaterEqual(len(first_calls), 2)
        self.assertEqual(len(second_calls), state["analysis_batch_count"] - 1)

    def test_output_boundary_blocks_regular_project_subdirectory(self):
        with self.assertRaises(GenerationBoundaryError):
            validate_output_boundary([EXAMPLE], EXAMPLE / "generated-kit")

    def test_output_boundary_allows_agentbridge_directory(self):
        validate_output_boundary([EXAMPLE], EXAMPLE / ".agentbridge" / "writing-kit")

    def test_cli_requires_ai_for_project_directory_without_no_ai(self):
        with tempfile.TemporaryDirectory() as tmp:
            stderr = StringIO()
            with patch.dict("os.environ", {}, clear=True), redirect_stderr(stderr):
                code = cli.main(["generate", str(EXAMPLE), "--output", str(Path(tmp) / "kit")])

            self.assertEqual(code, 1)
            self.assertIn("Project directory analysis requires an AI backend", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
