import json
import builtins
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from agentbridge.agent import AIGenerator, _extract_agent_result_text, _extract_agent_usage, _parse_json_object


class AgentProgressTests(unittest.TestCase):
    def test_extract_agent_result_text_reads_result_message_result(self):
        class FakeResultMessage:
            result = "你好，我可以帮你操作这个系统。"
            content = []

        text = _extract_agent_result_text(FakeResultMessage())

        self.assertEqual(text, "你好，我可以帮你操作这个系统。")

    def test_agent_runner_query_text_prefers_final_result_over_stream_text(self):
        class FakeAssistantMessage:
            content = [{"type": "text", "text": "draft response"}]
            result = None

        class FakeResultMessage:
            content = []
            result = "final response"
            usage = {"input_tokens": 120, "output_tokens": 30}

        class FakeRunner:
            async def query(self, _prompt):
                yield FakeAssistantMessage()
                yield FakeResultMessage()

        from agentbridge.agent import AgentRunner

        runner = AgentRunner.__new__(AgentRunner)
        runner.query = FakeRunner().query  # type: ignore[method-assign]

        self.assertEqual(runner.query_text("hello"), "final response")
        self.assertEqual(runner.last_usage["input_tokens"], 120)
        self.assertEqual(runner.last_usage["output_tokens"], 30)
        self.assertEqual(runner.last_usage["total_tokens"], 150)

    def test_extract_agent_usage_supports_sdk_result_metadata(self):
        class FakeResultMessage:
            usage = {
                "input_tokens": 100,
                "output_tokens": 25,
                "cache_read_input_tokens": 40,
                "cache_creation_input_tokens": 10,
            }
            total_cost_usd = 0.0123
            duration_ms = 456
            num_turns = 3

        usage = _extract_agent_usage(FakeResultMessage())

        self.assertEqual(usage["input_tokens"], 100)
        self.assertEqual(usage["output_tokens"], 25)
        self.assertEqual(usage["total_tokens"], 125)
        self.assertEqual(usage["cache_read_input_tokens"], 40)
        self.assertEqual(usage["cost_usd"], 0.0123)
        self.assertEqual(usage["duration_ms"], 456)
        self.assertEqual(usage["turns"], 3)

    def test_parse_json_object_prefers_generation_payload(self):
        text = (
            "intermediate note {} "
            '{"project_analysis": {"summary": "done"}, '
            '"tool_enhancements": {"create_character": {"description": "Create character"}}, '
            '"risk_assessments": {"create_character": {"risk": "write"}}, '
            '"additional_tools": [], "system_prompt": "", "skills": {}}'
        )

        parsed = _parse_json_object(text, {})

        self.assertEqual(parsed["project_analysis"]["summary"], "done")
        self.assertIn("create_character", parsed["tool_enhancements"])

    def test_generate_all_reports_source_files_and_provider_call(self):
        messages: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "app.py"
            source.write_text("print('hello')\n", encoding="utf-8")

            gen = AIGenerator(api_key="sk-test", base_url="https://api.deepseek.com/anthropic", model="deepseek-v4-flash", progress=messages.append)

            async def _fake_ask(*_args, **_kwargs):
                return (
                    "{"
                    '"project_analysis": {}, '
                    '"tool_enhancements": {}, '
                    '"risk_assessments": {}, '
                    '"additional_tools": [], '
                    '"system_prompt": "", '
                    '"skills": {}'
                    "}"
                )

            gen._ask = _fake_ask  # type: ignore[method-assign]
            gen.generate_all([], "kit", input_paths=[source])

        self.assertTrue(any("Added source file to AI context" in message for message in messages))
        self.assertTrue(any("Sending AI analysis request" in message for message in messages))
        self.assertTrue(any("Received AI analysis response" in message for message in messages))

    def test_generate_all_uses_agentic_sdk_with_compatible_base_url(self):
        messages: list[str] = []

        class FakeTextBlock:
            def __init__(self, text: str) -> None:
                self.type = "text"
                self.text = text

        class FakeToolUseBlock:
            def __init__(self, name: str, tool_input: dict[str, str]) -> None:
                self.type = "tool_use"
                self.name = name
                self.input = tool_input

        class FakeAssistantMessage:
            def __init__(self, content: list[object]) -> None:
                self.role = "assistant"
                self.content = content

        class FakeClaudeAgentOptions:
            last_kwargs: dict[str, object] | None = None

            def __init__(self, **kwargs: object) -> None:
                FakeClaudeAgentOptions.last_kwargs = kwargs

        async def fake_query(prompt: str, options: object):
            self.assertIn("Project paths to inspect read-only", prompt)
            self.assertIsNotNone(options)
            yield FakeAssistantMessage([
                FakeToolUseBlock("Read", {"file_path": "app.py"}),
                FakeTextBlock(
                    json.dumps(
                        {
                            "project_analysis": {},
                            "tool_enhancements": {},
                            "risk_assessments": {},
                            "additional_tools": [],
                            "system_prompt": "",
                            "skills": {},
                        }
                    )
                ),
            ])

        fake_module = types.ModuleType("claude_agent_sdk")
        fake_module.ClaudeAgentOptions = FakeClaudeAgentOptions
        fake_module.query = fake_query

        base_url = "https://api.deepseek.com/anthropic"
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {"claude_agent_sdk": fake_module}), patch.dict(os.environ, {"ANTHROPIC_BASE_URL": base_url}, clear=False):
            root = Path(tmp)
            (root / "app.py").write_text("print('hello')\n", encoding="utf-8")

            gen = AIGenerator(api_key="sk-test", progress=messages.append, analysis_mode="agentic")
            result = gen.generate_all([], "kit", input_paths=[root])

        self.assertEqual(FakeClaudeAgentOptions.last_kwargs["cwd"], str(root.resolve()))
        self.assertEqual(FakeClaudeAgentOptions.last_kwargs["base_url"], base_url)
        self.assertEqual(FakeClaudeAgentOptions.last_kwargs["tools"], ["Read", "Grep"])
        self.assertEqual(FakeClaudeAgentOptions.last_kwargs["allowed_tools"], ["Read", "Grep"])
        self.assertIn("Agent", FakeClaudeAgentOptions.last_kwargs["disallowed_tools"])
        self.assertTrue(any("Using Claude Agent SDK agentic analysis" in message for message in messages))
        self.assertTrue(any(base_url in message for message in messages))
        self.assertTrue(any("Claude Agent SDK reading file: app.py" in message for message in messages))
        self.assertTrue(any("Claude Agent SDK generated batch analysis JSON" in message for message in messages))
        self.assertEqual(result["system_prompt"], "")

    def test_agentic_backend_detection_does_not_import_claude_agent_sdk(self):
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name == "claude_agent_sdk":
                raise AssertionError("claude_agent_sdk should not be imported during backend detection")
            return original_import(name, *args, **kwargs)

        with patch("importlib.util.find_spec", return_value=object()), patch("builtins.__import__", side_effect=guarded_import):
            gen = AIGenerator(api_key="sk-test", analysis_mode="agentic")

        self.assertEqual(gen._backend, "agent-sdk")

    def test_agentic_progress_includes_tool_results_and_hidden_thinking(self):
        class FakeToolResultBlock:
            def __init__(self) -> None:
                self.type = "tool_result"
                self.tool_use_id = "call-1"
                self.content = {"path": "app.py", "content": "print('hello')\n"}

        class FakeThinkingBlock:
            def __init__(self) -> None:
                self.type = "thinking"
                self.text = "private reasoning"

        class FakeAssistantMessage:
            def __init__(self) -> None:
                self.role = "assistant"
                self.content = [FakeThinkingBlock(), FakeToolResultBlock()]

        messages: list[str] = []
        with patch("importlib.util.find_spec", return_value=object()):
            gen = AIGenerator(api_key="sk-test", progress=messages.append, analysis_mode="agentic")
        gen._report_agent_sdk_message(FakeAssistantMessage())

        self.assertTrue(any("internal reasoning step completed" in message for message in messages))
        self.assertTrue(any("tool result received" in message and "path=app.py" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
