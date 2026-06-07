from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from agentbridge.agent import AIGenerator
from agentbridge.chat import ChatConfig, ChatSession
from agentbridge.discovery import CapabilityDiscoverer
from agentbridge.generator import AgentKitGenerator
from agentbridge.mcp_server import MCPServerConfig, run_stdio_server
from agentbridge.runtime import DryRunError, dry_run
from agentbridge.static import StaticAIGenerator


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "discover":
            paths = [Path(p) for p in args.paths]
            capabilities = CapabilityDiscoverer().discover(paths)
            print(json.dumps([cap.to_dict() for cap in capabilities], indent=2, sort_keys=True))
            return 0
        if args.command == "generate":
            paths = [Path(p) for p in args.paths]
            ai_gen = _create_ai_generator(args)
            kit = AgentKitGenerator(ai_generator=ai_gen).generate(paths, Path(args.output), args.name)
            print(json.dumps(kit.to_manifest(), indent=2, sort_keys=True))
            return 0
        if args.command == "dry-run":
            result = dry_run(Path(args.kit), args.tool, json.loads(args.args), confirmed=args.confirmed)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["allowed"] or result["requires_confirmation"] else 2
        if args.command == "chat":
            return _run_chat(args)
        if args.command == "serve":
            return _run_mcp_server(args)
        if args.command == "web":
            return _run_web_chat(args)
    except (OSError, json.JSONDecodeError, DryRunError, ValueError, TypeError, RuntimeError) as exc:
        print(f"agentbridge: {exc}", file=sys.stderr)
        return 1
    parser.print_help()
    return 1


def _create_ai_generator(args: argparse.Namespace) -> AIGenerator:
    if getattr(args, "no_ai", False):
        return StaticAIGenerator()
    if not (getattr(args, "api_key", None) or os.environ.get("ANTHROPIC_API_KEY")):
        return StaticAIGenerator()
    try:
        return AIGenerator(
            api_key=getattr(args, "api_key", None),
            base_url=getattr(args, "base_url", None),
            model=getattr(args, "model", None),
        )
    except (ValueError, ImportError) as exc:
        print(f"agentbridge: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _run_chat(args: argparse.Namespace) -> int:
    kit_dir = Path(args.kit)
    if not kit_dir.exists():
        print(f"agentbridge: Kit directory not found: {kit_dir}", file=sys.stderr)
        return 1

    session = ChatSession(_chat_config_from_args(args))

    print(f"AgentBridge Chat — kit: {kit_dir}")
    print("Type /tools, /run <tool> key=value, confirm, cancel, or exit.\n")

    try:
        while True:
            try:
                user_input = input("You> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                break
            if not user_input or user_input.lower() == "exit":
                print("Goodbye!")
                break
            response = session.process(user_input)
            print(f"AgentBridge> {response.message}\n")
    except Exception as exc:
        print(f"\nSession ended: {exc}", file=sys.stderr)
    return 0


def _headers_from_args(args: argparse.Namespace) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in getattr(args, "header", None) or []:
        if "=" not in item:
            raise ValueError(f"Invalid --header value {item!r}; expected NAME=VALUE")
        key, value = item.split("=", 1)
        headers[key] = value
    if getattr(args, "bearer_token", None):
        headers["Authorization"] = f"Bearer {args.bearer_token}"
    return headers


def _chat_config_from_args(args: argparse.Namespace) -> ChatConfig:
    return ChatConfig(
        kit_dir=Path(args.kit),
        base_url=getattr(args, "base_url", None) or "",
        headers=_headers_from_args(args),
        execute=bool(getattr(args, "execute", False)),
        timeout=float(getattr(args, "timeout", 30.0)),
        user=getattr(args, "user", None) or "local",
        session_id=getattr(args, "session", None) or "default",
        memory_file=Path(args.memory_file) if getattr(args, "memory_file", None) else None,
        memory_enabled=not bool(getattr(args, "no_memory", False)),
    )


def _run_mcp_server(args: argparse.Namespace) -> int:
    config = MCPServerConfig(
        kit_dir=Path(args.kit),
        base_url=args.base_url or "",
        headers=_headers_from_args(args),
        execute=args.execute,
        timeout=args.timeout,
    )
    return run_stdio_server(config)


def _run_web_chat(args: argparse.Namespace) -> int:
    from agentbridge.web import run_web_chat

    config = _chat_config_from_args(args)
    return run_web_chat(config, host=args.host, port=args.port, allow_kit_switch=args.allow_kit_switch)


def _add_runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", help="Target system base URL for HTTP tools, for example http://localhost:8080.")
    parser.add_argument("--header", action="append", help="HTTP header for executed calls, as NAME=VALUE. May be repeated.")
    parser.add_argument("--bearer-token", help="Bearer token for executed HTTP calls.")
    parser.add_argument("--execute", action="store_true", help="Execute HTTP tools against --base-url. Without this, calls return dry-run plans only.")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds when --execute is enabled.")


def _add_chat_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--user", default="local", help="User id used for chat memory and audit context.")
    parser.add_argument("--session", default="default", help="Session id used to load and save chat memory.")
    parser.add_argument("--memory-file", help="JSON file for chat memory. Defaults to <kit>/.agentbridge-chat-memory.json.")
    parser.add_argument("--no-memory", action="store_true", help="Disable chat memory persistence.")


def _add_llm_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--api-key", help="LLM API key. Defaults to ANTHROPIC_API_KEY env var.")
    parser.add_argument("--base-url", help="Custom LLM API endpoint. Defaults to ANTHROPIC_BASE_URL env var. Examples: https://api.deepseek.com/anthropic, https://openrouter.ai/api/v1")
    parser.add_argument("--model", help="LLM model name. Defaults to ANTHROPIC_MODEL env var or claude-sonnet-4-20250514. Examples: deepseek-v4-flash, claude-sonnet-4-20250514")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentbridge", description="Generate Agent Integration Kits for existing systems. Requires LLM API key.")
    subparsers = parser.add_subparsers(dest="command")

    discover = subparsers.add_parser("discover", help="Discover system capabilities (no API key needed).")
    discover.add_argument("paths", nargs="+", help="Files or directories to inspect.")

    generate = subparsers.add_parser("generate", help="Generate an Agent Integration Kit. Uses AI when an API key is configured.")
    generate.add_argument("paths", nargs="+", help="Files or directories to inspect.")
    generate.add_argument("--output", "-o", required=True, help="Output directory for the generated kit.")
    generate.add_argument("--name", help="Kit name. Defaults to the input directory name.")
    generate.add_argument("--no-ai", action="store_true", help="Generate deterministically without LLM enrichment.")
    _add_llm_options(generate)

    dry = subparsers.add_parser("dry-run", help="Dry-run a generated tool invocation.")
    dry.add_argument("kit", help="Generated kit directory.")
    dry.add_argument("tool", help="Tool name.")
    dry.add_argument("--args", default="{}", help="JSON arguments for the tool.")
    dry.add_argument("--confirmed", action="store_true", help="Mark high-risk operation as human-confirmed.")

    chat = subparsers.add_parser("chat", help="Start an interactive chat over a generated kit and MCP runtime.")
    chat.add_argument("kit", help="Generated kit directory.")
    _add_runtime_options(chat)
    _add_chat_options(chat)

    serve = subparsers.add_parser("serve", help="Run a generated kit as a stdio MCP server.")
    serve.add_argument("kit", help="Generated kit directory.")
    _add_runtime_options(serve)

    web = subparsers.add_parser("web", help="Run a browser chat UI over a generated kit and MCP runtime.")
    web.add_argument("kit", help="Generated kit directory.")
    web.add_argument("--host", default="127.0.0.1", help="Host for the Web chat server.")
    web.add_argument("--port", type=int, default=8765, help="Port for the Web chat server. Use 0 for an available port.")
    web.add_argument("--allow-kit-switch", action="store_true", help="Allow the Web UI to switch kit directories per session.")
    _add_runtime_options(web)
    _add_chat_options(web)

    return parser


if __name__ == "__main__":
    raise SystemExit(main())
