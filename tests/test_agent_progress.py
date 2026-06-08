import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from agentbridge.agent import AIGenerator


class AgentProgressTests(unittest.TestCase):
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

    def test_generate_all_uses_agentic_sdk_and_reports_tool_progress(self):
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

        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {"claude_agent_sdk": fake_module}), patch.dict(os.environ, {"ANTHROPIC_BASE_URL": ""}, clear=False):
            root = Path(tmp)
            (root / "app.py").write_text("print('hello')\n", encoding="utf-8")

            gen = AIGenerator(api_key="sk-test", progress=messages.append, analysis_mode="agentic")
            result = gen.generate_all([], "kit", input_paths=[root])

        self.assertEqual(FakeClaudeAgentOptions.last_kwargs["cwd"], str(root.resolve()))
        self.assertTrue(any("Using Claude Agent SDK agentic analysis" in message for message in messages))
        self.assertTrue(any("Claude Agent SDK tool call: Read" in message for message in messages))
        self.assertTrue(any("Claude Agent SDK assistant text received" in message for message in messages))
        self.assertEqual(result["system_prompt"], "")


if __name__ == "__main__":
    unittest.main()
