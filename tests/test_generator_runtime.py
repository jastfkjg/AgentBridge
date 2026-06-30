import tempfile
import sys
import types
import unittest
import time
import threading
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
import json

from agentbridge import cli
from agentbridge.agent import AIGenerator
from agentbridge.discovery import CapabilityDiscoverer
from agentbridge.generator import AgentKitGenerator, GenerationBoundaryError, build_generated_test_file, validate_output_boundary
from agentbridge.models import Capability, SourceRef
from agentbridge.runtime import dry_run


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "writing_system"


def _make_mock_generator() -> AIGenerator:
    gen = MagicMock(spec=AIGenerator)
    gen.api_key = "sk-test-mock-key"
    gen.base_url = ""
    gen.model = "mock-model"
    gen._backend = "anthropic"
    gen.uses_agentic_analysis.return_value = False

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
            self.assertTrue((output / "analysis" / "report.md").exists())
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
            self.assertEqual(manifest["outputs"]["analysis_report"], "analysis/report.md")
            report = (output / "analysis" / "report.md").read_text(encoding="utf-8")
            self.assertIn("Capability Summary", report)
            self.assertIn("Risk Summary", report)

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
            self.assertFalse(result["requires_confirmation"])
            self.assertEqual(result["error"]["code"], "permission_denied")

    def test_dry_run_denies_confirmed_destructive_action_by_default(self):
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

            self.assertFalse(result["allowed"])
            self.assertEqual(result["error"]["code"], "permission_denied")
            self.assertEqual(result["would_execute"], False)

    def test_generator_requires_ai_generator(self):
        with self.assertRaises(TypeError):
            AgentKitGenerator()

    def test_agentic_default_batch_size_is_smaller(self):
        agentic = _make_mock_generator()
        agentic.analysis_mode = "agentic"
        prompt = _make_mock_generator()
        prompt.analysis_mode = "prompt"

        self.assertEqual(AgentKitGenerator(ai_generator=agentic).analysis_batch_size, 10)
        self.assertEqual(AgentKitGenerator(ai_generator=prompt).analysis_batch_size, 30)

    def test_status_progress_writes_last_sdk_event(self):
        gen = _make_mock_generator()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            output.mkdir()
            generator = AgentKitGenerator(ai_generator=gen, progress=lambda _message: None)
            emit = generator._status_progress(
                output,
                "planning",
                "Planning project understanding.",
                kit_name="writing-kit",
                candidate_capability_count=21,
            )
            emit("Claude Agent SDK reading file: app.py")
            status = json.loads((output / "generation_status.json").read_text(encoding="utf-8"))

        self.assertEqual(status["status"], "planning")
        self.assertEqual(status["last_sdk_event"], "Claude Agent SDK reading file: app.py")

    def test_generate_calls_ai_generator(self):
        gen = _make_mock_generator()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            AgentKitGenerator(ai_generator=gen).generate([EXAMPLE], output, name="writing-kit")
            gen.generate_all.assert_called_once()

    def test_generator_repairs_or_skips_invalid_ai_capability_objects(self):
        gen = _make_mock_generator()
        discoverer = CapabilityDiscoverer()
        raw_caps = discoverer.discover([EXAMPLE])

        def generate_all(caps, _kit_name, input_paths=None):
            valid_extra = raw_caps[0].to_dict()
            valid_extra["name"] = "inspect_project_health"
            valid_extra["domain"] = "inspection"
            valid_extra["resource"] = "project_health"
            valid_extra["action"] = "inspect"
            valid_extra["source"] = {"kind": "ai_inferred", "path": "", "location": "mock"}
            valid_extra["transport"] = {"type": "inferred", "operation": "inspect_project_health"}
            return {
                "enhanced_capabilities": [
                    {"description": "missing required fields"},
                    valid_extra,
                ],
                "rule_signals": {"candidate_capabilities": [cap.to_dict() for cap in caps]},
                "agent_analysis": {"summary": "mock", "assumptions": []},
                "system_prompt": "",
                "skills": {},
            }

        gen.generate_all.side_effect = generate_all
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            kit = AgentKitGenerator(ai_generator=gen).generate([EXAMPLE], output, name="writing-kit")
            analysis = json.loads((output / "analysis" / "agent_analysis.json").read_text(encoding="utf-8"))

        self.assertIn("inspect_project_health", {cap.name for cap in kit.capabilities})
        self.assertTrue(any("Skipped invalid AI capability" in item for item in analysis["assumptions"]))

    def test_generator_merges_existing_kit_capabilities_before_ai_analysis(self):
        gen = _make_mock_generator()
        captured: list[Capability] = []

        def generate_all(caps, _kit_name, input_paths=None):
            captured.extend(caps)
            return {
                "enhanced_capabilities": list(caps),
                "rule_signals": {"candidate_capabilities": [cap.to_dict() for cap in caps]},
                "agent_analysis": {},
                "system_prompt": "",
                "skills": {},
            }

        gen.generate_all.side_effect = generate_all
        existing = Capability(
            name="legacy_workflow",
            domain="legacy",
            resource="workflow",
            action="run",
            description="Existing AI-inferred workflow",
            input_schema={"type": "object", "properties": {}, "required": []},
            risk="read",
            confirm_required=False,
            source=SourceRef("ai_inferred", "", "existing kit"),
            transport={"type": "inferred"},
        )

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            AgentKitGenerator(
                ai_generator=gen,
                existing_capabilities=[existing],
            ).generate([EXAMPLE], output, name="writing-kit")

        self.assertIn("legacy_workflow", {cap.name for cap in captured})

    def test_generator_preserves_user_authored_prompt_and_policy_overrides(self):
        gen = _make_mock_generator()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            (output / "prompts").mkdir(parents=True)
            (output / "guardrails").mkdir(parents=True)
            (output / "prompts" / "system.md").write_text("# User Prompt\n\nKeep this.", encoding="utf-8")
            (output / "guardrails" / "permissions.json").write_text(
                json.dumps(
                    {
                        "policy": {"risk_actions": {"destructive": "confirm"}},
                        "tools": {
                            "delete_character": {
                                "risk": "destructive",
                                "confirm_required": True,
                                "reason": "User override",
                                "transport": {"type": "http", "method": "DELETE", "path": "/characters/{character_id}"},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            AgentKitGenerator(ai_generator=gen).generate([EXAMPLE], output, name="writing-kit")

            prompt = (output / "prompts" / "system.md").read_text(encoding="utf-8")
            guardrails = json.loads((output / "guardrails" / "permissions.json").read_text(encoding="utf-8"))
            preserved = json.loads((output / "analysis" / "preserved_user_files.json").read_text(encoding="utf-8"))

        self.assertEqual(prompt, "# User Prompt\n\nKeep this.")
        self.assertEqual(guardrails["policy"]["risk_actions"]["destructive"], "confirm")
        self.assertIn("prompts/system.md", preserved["preserved"])

    def test_generated_tool_contract_tests_cover_policy_and_dry_run(self):
        content = build_generated_test_file()

        self.assertIn("permission_denied", content)
        self.assertIn("dry_run", content)
        self.assertIn("write", content)
        self.assertIn("destructive", content)

    def test_parser_exposes_existing_kit_enhance_command(self):
        args = cli.build_parser().parse_args(
            ["enhance", "build/kit", "project", "--api-key", "sk-test"]
        )

        self.assertEqual(args.command, "enhance")
        self.assertEqual(args.kit, "build/kit")
        self.assertEqual(args.paths, ["project"])

    def test_enhance_forces_claude_agent_sdk_analysis(self):
        existing = CapabilityDiscoverer().discover([EXAMPLE])[0]

        with tempfile.TemporaryDirectory() as tmp:
            kit_dir = Path(tmp) / "kit"
            kit_dir.mkdir()
            (kit_dir / "capabilities.json").write_text(
                json.dumps([existing.to_dict()]),
                encoding="utf-8",
            )
            (kit_dir / "manifest.json").write_text(
                json.dumps({"name": "existing-kit"}),
                encoding="utf-8",
            )
            args = cli.build_parser().parse_args(
                ["enhance", str(kit_dir), str(EXAMPLE), "--api-key", "sk-test"]
            )
            generated_kit = MagicMock()
            generated_kit.to_manifest.return_value = {"name": "existing-kit"}
            generator_instance = MagicMock()
            generator_instance.generate.return_value = generated_kit
            report = MagicMock(ok=True)

            with (
                patch("agentbridge.cli._claude_agent_sdk_installed", return_value=True),
                patch("agentbridge.cli.AIGenerator") as ai_generator,
                patch("agentbridge.cli.AgentKitGenerator", return_value=generator_instance),
                patch("agentbridge.cli.validate_kit", return_value=report),
                patch("agentbridge.cli.format_report", return_value="Kit valid"),
                patch("sys.stdout", new=StringIO()),
            ):
                result = cli._run_enhance(args)

            self.assertEqual(result, 0)
            self.assertEqual(ai_generator.call_args.kwargs["analysis_mode"], "agentic")
            self.assertNotIn("no_ai", ai_generator.call_args.kwargs)
            generator_instance.generate.assert_called_once_with(
                [EXAMPLE],
                kit_dir,
                "existing-kit",
            )

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
        messages: list[str] = []

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            output.mkdir()
            generator = AgentKitGenerator(
                ai_generator=gen,
                progress=messages.append,
                progress_interval=0.01,
            )
            stop_event = threading.Event()
            status_lock = threading.Lock()
            stopper = threading.Timer(0.05, stop_event.set)
            stopper.start()
            generator._analysis_heartbeat(stop_event, output, "writing-kit", 21, 1, 2, 5, status_lock)
            stopper.cancel()

            status = json.loads((output / "generation_status.json").read_text(encoding="utf-8"))

        self.assertTrue(any("Still waiting for AI batch" in message for message in messages))
        self.assertEqual(status["status"], "analyzing")

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

    def test_agentic_batches_receive_project_paths_instead_of_file_slices(self):
        gen = _make_mock_generator()
        gen.uses_agentic_analysis.return_value = True
        raw_caps = CapabilityDiscoverer().discover([EXAMPLE])
        preferred = raw_caps[-1].name
        gen.plan_agentic_analysis.return_value = {
            "project_summary": "SDK-led project plan.",
            "main_capability_names": [preferred],
            "questions": ["Which domain should be prioritized?"],
            "notes_for_generation": "Prioritize the writing workflow.",
        }
        captured_input_paths: list[list[Path]] = []
        captured_batches: list[list[Any]] = []

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
                    "summary": "Agentic mock analysis.",
                    "business_objects": [],
                    "workflows": [],
                    "permission_boundaries": [],
                    "side_effects": [],
                    "assumptions": [],
                },
                "system_prompt": "# Agentic Mock",
                "skills": {},
            }

        gen.generate_all.side_effect = _generate_all

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            AgentKitGenerator(
                ai_generator=gen,
                progress=lambda _message: None,
                analysis_capability_limit=5,
                answer_agentic_questions=lambda _context: {"q1": "writing"},
            ).generate([EXAMPLE], output, name="writing-kit")
            plan = json.loads((output / "analysis" / "agentic_plan.json").read_text(encoding="utf-8"))

        self.assertEqual(captured_batches[0][0].name, preferred)
        self.assertEqual(plan["user_answers"], {"q1": "writing"})
        self.assertGreater(len(captured_input_paths), 1)
        self.assertTrue(all(paths == [EXAMPLE] for paths in captured_input_paths))
        gen.set_agentic_guidance.assert_called()

    def test_agentic_batch_failure_uses_local_basic_analysis(self):
        gen = _make_mock_generator()
        gen.uses_agentic_analysis.return_value = True
        gen.plan_agentic_analysis.return_value = {
            "project_summary": "SDK-led project plan.",
            "main_capability_names": [],
            "questions": [],
            "notes_for_generation": "",
        }
        gen.generate_all.side_effect = RuntimeError("mock sdk timeout")
        messages: list[str] = []

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            AgentKitGenerator(
                ai_generator=gen,
                progress=messages.append,
                analysis_capability_limit=5,
            ).generate([EXAMPLE], output, name="writing-kit")

            state = json.loads((output / "analysis" / "resume_state.json").read_text(encoding="utf-8"))
            status = json.loads((output / "generation_status.json").read_text(encoding="utf-8"))
            batch = json.loads((output / "analysis" / "batches" / "batch_0001.json").read_text(encoding="utf-8"))
            analysis = json.loads((output / "analysis" / "agent_analysis.json").read_text(encoding="utf-8"))
            manifest_exists = (output / "manifest.json").exists()

        self.assertTrue(manifest_exists)
        self.assertEqual(batch["status"], "complete")
        self.assertTrue(batch["local_basic"])
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["fallback_batches"], [])
        self.assertEqual(state["remaining_batch_count"], 0)
        self.assertEqual(status["status"], "complete")
        self.assertIn("Merged batch analysis", analysis["summary"])
        self.assertTrue(any("using local basic project analysis" in message for message in messages))

    def test_generate_resume_retries_local_basic_batches_with_ai_generator(self):
        first_gen = _make_mock_generator()
        first_gen.uses_agentic_analysis.return_value = True
        first_gen.plan_agentic_analysis.return_value = {
            "project_summary": "SDK-led project plan.",
            "main_capability_names": [],
            "questions": [],
            "notes_for_generation": "",
        }
        first_gen.generate_all.side_effect = RuntimeError("mock sdk timeout")

        second_gen = _make_mock_generator()
        second_gen.uses_agentic_analysis.return_value = True
        second_calls: list[list[Any]] = []

        def _second_run(caps, _kit_name, input_paths=None):
            second_calls.append(list(caps))
            return {
                "enhanced_capabilities": list(caps),
                "rule_signals": {
                    "candidate_capabilities": [cap.to_dict() for cap in caps],
                    "risk_policy": {},
                },
                "agent_analysis": {
                    "summary": "Recovered agentic batch.",
                    "business_objects": [],
                    "workflows": [],
                    "permission_boundaries": [],
                    "side_effects": [],
                    "assumptions": [],
                },
                "system_prompt": "# Recovered Agentic Batch",
                "skills": {},
            }

        second_gen.generate_all.side_effect = _second_run

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            AgentKitGenerator(
                ai_generator=first_gen,
                progress=lambda _message: None,
                analysis_capability_limit=5,
            ).generate([EXAMPLE], output, name="writing-kit")

            AgentKitGenerator(
                ai_generator=second_gen,
                progress=lambda _message: None,
                analysis_capability_limit=5,
                resume=True,
            ).generate([EXAMPLE], output, name="writing-kit")

            state = json.loads((output / "analysis" / "resume_state.json").read_text(encoding="utf-8"))

        self.assertGreaterEqual(len(second_calls), 1)
        self.assertEqual(state["fallback_batch_count"], 0)

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

    def test_generate_can_stop_after_primary_batch(self):
        gen = _make_mock_generator()
        calls: list[list[Any]] = []

        def _generate_all(caps, _kit_name, input_paths=None):
            calls.append(list(caps))
            return {
                "enhanced_capabilities": list(caps),
                "rule_signals": {
                    "candidate_capabilities": [cap.to_dict() for cap in caps],
                    "risk_policy": {},
                },
                "agent_analysis": {
                    "summary": "Primary batch only.",
                    "business_objects": [],
                    "workflows": [],
                    "permission_boundaries": [],
                    "side_effects": [],
                    "assumptions": [],
                },
                "system_prompt": "# Primary Batch Only",
                "skills": {},
            }

        gen.generate_all.side_effect = _generate_all

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            AgentKitGenerator(
                ai_generator=gen,
                progress=lambda _message: None,
                analysis_capability_limit=5,
                confirm_remaining_analysis=lambda _context: False,
            ).generate([EXAMPLE], output, name="writing-kit")

            state = json.loads((output / "analysis" / "resume_state.json").read_text(encoding="utf-8"))
            status = json.loads((output / "generation_status.json").read_text(encoding="utf-8"))
            self.assertTrue((output / "capabilities.json").exists())

        self.assertEqual(len(calls), 1)
        self.assertEqual(state["status"], "partial")
        self.assertEqual(status["status"], "partial_complete")
        self.assertGreater(state["remaining_batch_count"], 0)

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
            self.assertIn("Starting generation", stderr.getvalue())
            self.assertIn("Project directory analysis requires an AI backend", stderr.getvalue())

    def test_cli_agentic_without_api_key_requires_real_sdk_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "kit"
            stderr = StringIO()
            with patch.dict("os.environ", {}, clear=True), redirect_stderr(stderr):
                code = cli.main([
                    "generate",
                    str(EXAMPLE),
                    "--output",
                    str(output),
                    "--analysis-mode",
                    "agentic",
                    "--batch-size",
                    "5",
                    "--resume",
                    "--yes",
                ])

        self.assertEqual(code, 1)
        self.assertFalse((output / "generation_status.json").exists())
        self.assertIn("ANTHROPIC_API_KEY is required when --analysis-mode agentic", stderr.getvalue())

    def test_cli_recommends_claude_agent_sdk_when_missing(self):
        args = cli.build_parser().parse_args([
            "generate",
            str(EXAMPLE),
            "--output",
            "unused",
            "--api-key",
            "sk-test",
            "--analysis-mode",
            "prompt",
        ])
        stderr = StringIO()
        fake_anthropic = types.ModuleType("anthropic")

        with patch("agentbridge.cli._claude_agent_sdk_installed", return_value=False), patch.dict(sys.modules, {"anthropic": fake_anthropic}), redirect_stderr(stderr):
            gen = cli._create_ai_generator(args, [EXAMPLE])

        self.assertIsInstance(gen, AIGenerator)
        self.assertIn("Claude Agent SDK is the recommended primary route", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
