from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from agentbridge.agent import AIGenerator
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
            return asyncio.run(_run_chat(args))
        if args.command == "serve":
            return _run_mcp_server(args)
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


async def _run_chat(args: argparse.Namespace) -> int:
    try:
        from agentbridge.agent import AgentRunner
    except ImportError as exc:
        print(
            "agentbridge: Agent chat requires the 'claude-agent-sdk' package.\n"
            "Install with: pip install agbr[agent]",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    kit_dir = Path(args.kit)
    if not kit_dir.exists():
        print(f"agentbridge: Kit directory not found: {kit_dir}", file=sys.stderr)
        return 1

    try:
        runner = AgentRunner(
            kit_dir=str(kit_dir),
            api_key=getattr(args, "api_key", None),
            base_url=getattr(args, "base_url", None),
            model=getattr(args, "model", None),
        )
    except ValueError as exc:
        print(f"agentbridge: {exc}", file=sys.stderr)
        return 1

    print(f"AgentBridge Chat — kit: {kit_dir}")
    print("Type 'exit' or Ctrl+C to quit.\n")

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
            try:
                async for message in runner.query(user_input):
                    print(message)
            except Exception as exc:
                print(f"Agent error: {exc}", file=sys.stderr)
    except Exception as exc:
        print(f"\nSession ended: {exc}", file=sys.stderr)
    return 0


def _run_mcp_server(args: argparse.Namespace) -> int:
    headers: dict[str, str] = {}
    for item in args.header or []:
        if "=" not in item:
            raise ValueError(f"Invalid --header value {item!r}; expected NAME=VALUE")
        key, value = item.split("=", 1)
        headers[key] = value
    if args.bearer_token:
        headers["Authorization"] = f"Bearer {args.bearer_token}"
    config = MCPServerConfig(
        kit_dir=Path(args.kit),
        base_url=args.base_url or "",
        headers=headers,
        execute=args.execute,
        timeout=args.timeout,
    )
    return run_stdio_server(config)


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

    chat = subparsers.add_parser("chat", help="Start an interactive AI agent session. Requires API key.")
    chat.add_argument("kit", help="Generated kit directory.")
    _add_llm_options(chat)

    serve = subparsers.add_parser("serve", help="Run a generated kit as a stdio MCP server.")
    serve.add_argument("kit", help="Generated kit directory.")
    serve.add_argument("--base-url", help="Target system base URL for HTTP tools, for example http://localhost:8080.")
    serve.add_argument("--header", action="append", help="HTTP header for executed calls, as NAME=VALUE. May be repeated.")
    serve.add_argument("--bearer-token", help="Bearer token for executed HTTP calls.")
    serve.add_argument("--execute", action="store_true", help="Execute HTTP tools against --base-url. Without this, calls return dry-run plans only.")
    serve.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds when --execute is enabled.")

    return parser


if __name__ == "__main__":
    raise SystemExit(main())
