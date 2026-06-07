import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentbridge.client_config import MCPClientConfig, build_mcp_client_configs, build_server_args
from agentbridge.cli import main


class ClientConfigTests(unittest.TestCase):
    def test_builds_stdio_server_args_with_safe_bearer_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp) / "kit"
            config = MCPClientConfig(
                kit_dir=kit,
                server_name="Writing Kit",
                base_url="http://localhost:8080",
                bearer_env="API_TOKEN",
                execute=True,
                read_only=True,
                deny_risks=["destructive"],
            )

            args = build_server_args(config)

            self.assertIn(str(kit.resolve()), args)
            self.assertIn("--bearer-env", args)
            self.assertIn("API_TOKEN", args)
            self.assertNotIn("--bearer-token", args)
            self.assertIn("--read-only", args)
            self.assertEqual(args[-2:], ["--deny-risk", "destructive"])

    def test_builds_claude_and_codex_config_snippets(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp) / "kit"
            config = MCPClientConfig(kit_dir=kit, server_name="Writing Kit")

            snippets = build_mcp_client_configs(config)

            self.assertIn("Writing-Kit", snippets["claude_desktop"]["mcpServers"])
            self.assertIn("[mcp_servers.Writing-Kit]", snippets["codex_toml"])

    def test_cli_mcp_config_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp) / "kit"
            kit.mkdir()

            out = io.StringIO()
            with patch("sys.stdout", out):
                code = main(["mcp-config", str(kit), "--json", "--bearer-env", "API_TOKEN"])

            self.assertEqual(code, 0)
            data = json.loads(out.getvalue())
            args = data["generic_json"]["mcpServers"]["agentbridge"]["args"]
            self.assertIn("--bearer-env", args)


if __name__ == "__main__":
    unittest.main()
