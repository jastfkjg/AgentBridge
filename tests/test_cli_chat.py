import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from agentbridge.cli import _chat_config_from_args, _find_tool_selection, _format_usage, _print_chat_response, build_parser


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

    def test_chat_parser_accepts_llm_and_adapter_runtime_options(self):
        parser = build_parser()

        args = parser.parse_args(
            [
                "chat",
                "/tmp/kit",
                "--api-key",
                "sk-test",
                "--model",
                "claude-test",
                "--llm-base-url",
                "https://llm.example.test",
                "--llm-timeout",
                "42",
                "--graphql-endpoint",
                "https://api.example.test/graphql",
                "--database-url",
                "sqlite:///tmp/app.db",
                "--grpc-target",
                "127.0.0.1:50051",
            ]
        )

        config = _chat_config_from_args(args)

        self.assertEqual(config.llm_api_key, "sk-test")
        self.assertEqual(config.llm_model, "claude-test")
        self.assertEqual(config.llm_base_url, "https://llm.example.test")
        self.assertEqual(config.llm_timeout, 42)
        self.assertEqual(config.graphql_endpoint, "https://api.example.test/graphql")
        self.assertEqual(config.database_url, "sqlite:///tmp/app.db")
        self.assertEqual(config.grpc_target, "127.0.0.1:50051")


if __name__ == "__main__":
    unittest.main()
