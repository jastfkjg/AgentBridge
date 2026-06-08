import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
