import unittest

from agentbridge.agent import _parse_json_array, _parse_json_object


class AgentJsonParsingTests(unittest.TestCase):
    def test_parse_json_object_allows_braces_inside_strings(self):
        text = (
            "```json\n"
            "{"
            '"system_prompt": "Use JSON args like {\\"project_id\\": \\"p1\\"}.", '
            '"skills": {"writing": "Check templates such as {{title}} before running."}'
            "}"
            "\n```"
        )

        parsed = _parse_json_object(text, {})

        self.assertEqual(
            parsed["system_prompt"],
            'Use JSON args like {"project_id": "p1"}.',
        )
        self.assertIn("{{title}}", parsed["skills"]["writing"])

    def test_parse_json_object_skips_non_json_braces_before_payload(self):
        text = "Model note: use {placeholder}.\nActual payload: {\"ok\": true}"

        parsed = _parse_json_object(text, {})

        self.assertEqual(parsed, {"ok": True})

    def test_parse_json_array_allows_braces_inside_strings(self):
        text = 'Result: ["literal {brace}", {"name": "tool"}]'

        parsed = _parse_json_array(text, [])

        self.assertEqual(parsed, ["literal {brace}", {"name": "tool"}])


if __name__ == "__main__":
    unittest.main()
