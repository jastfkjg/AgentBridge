import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from agentbridge.cli import _find_tool_selection, _format_usage, _print_chat_response


class CLIChatTests(unittest.TestCase):
    def test_selects_tool_by_number_or_name(self):
        tools = [{"name": "list_script"}, {"name": "create_character"}]

        self.assertEqual(_find_tool_selection(tools, "2")["name"], "create_character")
        self.assertEqual(_find_tool_selection(tools, "list_script")["name"], "list_script")
        self.assertIsNone(_find_tool_selection(tools, "9"))

    def test_formats_last_and_session_token_usage(self):
        text = _format_usage(
            {
                "input_tokens": 1200,
                "output_tokens": 300,
                "total_tokens": 1500,
                "session_total_tokens": 4200,
            }
        )

        self.assertIn("1,200 input", text)
        self.assertIn("300 output", text)
        self.assertIn("session 4,200 tokens", text)

    def test_high_risk_response_prompts_authorize_or_cancel(self):
        follow_up = SimpleNamespace(message="Operation authorized.")

        class FakeSession:
            def __init__(self):
                self.confirmed = False

            def confirm(self):
                self.confirmed = True
                return follow_up

            def process(self, _message):
                raise AssertionError("cancel should not be selected")

        session = FakeSession()
        response = SimpleNamespace(
            message="Delete character requires confirmation.",
            usage={},
            status="needs_confirmation",
        )

        output = io.StringIO()
        with patch("builtins.input", return_value="1"), redirect_stdout(output):
            _print_chat_response(session, response)

        self.assertTrue(session.confirmed)
        self.assertIn("[1] Authorize", output.getvalue())
        self.assertIn("Operation authorized.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
