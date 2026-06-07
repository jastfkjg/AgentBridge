from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MCPClientConfig:
    kit_dir: Path
    server_name: str = "agentbridge"
    command: str = "agentbridge"
    base_url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    bearer_token: str = ""
    bearer_env: str = ""
    execute: bool = False
    timeout: float = 30.0
    read_only: bool = False
    deny_risks: list[str] = field(default_factory=list)
    allow_tools: list[str] = field(default_factory=list)
    audit_log: Path | None = None


def safe_server_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip()).strip("-")
    return cleaned or "agentbridge"


def build_server_args(config: MCPClientConfig) -> list[str]:
    args = ["serve", str(config.kit_dir.resolve())]
    if config.base_url:
        args.extend(["--base-url", config.base_url])
    for key, value in sorted(config.headers.items()):
        args.extend(["--header", f"{key}={value}"])
    if config.bearer_token:
        args.extend(["--bearer-token", config.bearer_token])
    if config.bearer_env:
        args.extend(["--bearer-env", config.bearer_env])
    if config.execute:
        args.append("--execute")
    if config.timeout != 30.0:
        args.extend(["--timeout", str(config.timeout)])
    if config.read_only:
        args.append("--read-only")
    for risk in config.deny_risks:
        args.extend(["--deny-risk", risk])
    for tool in config.allow_tools:
        args.extend(["--allow-tool", tool])
    if config.audit_log:
        args.extend(["--audit-log", str(config.audit_log)])
    return args


def build_mcp_client_configs(config: MCPClientConfig) -> dict[str, Any]:
    name = safe_server_name(config.server_name)
    server = {"command": config.command, "args": build_server_args(config)}
    return {
        "claude_desktop": {"mcpServers": {name: server}},
        "claude_code_project": {"mcpServers": {name: server}},
        "generic_json": {"mcpServers": {name: server}},
        "codex_toml": build_codex_toml(name, server),
    }


def build_codex_toml(name: str, server: dict[str, Any]) -> str:
    args = server.get("args", [])
    lines = [
        f"[mcp_servers.{quote_toml_key(name)}]",
        f"command = {json.dumps(server.get('command', 'agentbridge'))}",
        f"args = {json.dumps(args)}",
        "",
    ]
    return "\n".join(lines)


def quote_toml_key(key: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", key):
        return key
    return json.dumps(key)


def format_mcp_client_configs(config: MCPClientConfig) -> str:
    configs = build_mcp_client_configs(config)
    return "\n\n".join(
        [
            "Claude Desktop / Claude Code .mcp.json:",
            json.dumps(configs["claude_desktop"], indent=2, sort_keys=True),
            "Codex CLI ~/.codex/config.toml:",
            str(configs["codex_toml"]).rstrip(),
        ]
    )


def build_clients_readme(kit_name: str, config: MCPClientConfig | None = None) -> str:
    default_config = config or MCPClientConfig(kit_dir=Path("."))
    snippet = format_mcp_client_configs(default_config)
    return f"""# MCP Client Setup

This directory contains ready-to-copy MCP client snippets for the `{kit_name}` kit.

AgentBridge runs as a local stdio MCP server. The generated command starts:

```bash
agentbridge serve <kit>
```

By default, the server is dry-run only and will not call the target system. Add `--execute` and a target `--base-url` only after validating the kit and reviewing its guardrails.

Prefer `--bearer-env API_TOKEN_ENV` over writing secrets directly into config files.

## Generated Snippets

```text
{snippet}
```
"""
