from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from agentbridge.agent import AIGenerator
from agentbridge.chat import ChatConfig, ChatSession
from agentbridge.client_config import MCPClientConfig, build_mcp_client_configs, format_mcp_client_configs
from agentbridge.discovery import CapabilityDiscoverer
from agentbridge.generator import AgentKitGenerator
from agentbridge.kit import doctor_kit, format_report, validate_kit
from agentbridge.models import Capability
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
            ai_gen = _create_ai_generator(args, paths)
            kit = AgentKitGenerator(
                ai_generator=ai_gen,
                progress=_print_progress,
                confirm_ai_analysis=_build_ai_confirmation(args),
                progress_interval=getattr(args, "progress_interval", None),
                analysis_batch_size=getattr(args, "batch_size", None),
                resume=bool(getattr(args, "resume", False)),
            ).generate(
                paths,
                Path(args.output),
                args.name,
            )
            print(json.dumps(kit.to_manifest(), indent=2, sort_keys=True))
            return 0
        if args.command == "init":
            return _run_init(args)
        if args.command == "dry-run":
            result = dry_run(Path(args.kit), args.tool, json.loads(args.args), confirmed=args.confirmed)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["allowed"] or result["requires_confirmation"] else 2
        if args.command == "validate":
            report = validate_kit(Path(args.kit))
            _print_report(report, args.json)
            return 0 if report.ok else 2
        if args.command == "doctor":
            report = doctor_kit(Path(args.kit), base_url=args.base_url or "", execute=args.execute)
            _print_report(report, args.json)
            return 0 if report.ok else 2
        if args.command == "mcp-config":
            return _run_mcp_config(args)
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


def _create_ai_generator(args: argparse.Namespace, paths: list[Path] | None = None) -> AIGenerator:
    if getattr(args, "no_ai", False):
        return StaticAIGenerator()
    if not (getattr(args, "api_key", None) or os.environ.get("ANTHROPIC_API_KEY")):
        if any(path.is_dir() for path in paths or []):
            raise ValueError(
                "Project directory analysis requires an AI backend. "
                "Set ANTHROPIC_API_KEY or pass --api-key. "
                "Use --no-ai only for deterministic schema-only kit generation."
            )
        return StaticAIGenerator()
    try:
        return AIGenerator(
            api_key=getattr(args, "api_key", None),
            base_url=getattr(args, "base_url", None),
            model=getattr(args, "model", None),
            timeout=getattr(args, "llm_timeout", None),
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


def _run_init(args: argparse.Namespace) -> int:
    paths = [Path(p) for p in args.paths]
    output = Path(args.output)
    ai_gen = _create_ai_generator(args, paths)
    kit = AgentKitGenerator(
        ai_generator=ai_gen,
        progress=_print_progress,
        confirm_ai_analysis=_build_ai_confirmation(args),
        progress_interval=getattr(args, "progress_interval", None),
        analysis_batch_size=getattr(args, "batch_size", None),
        resume=bool(getattr(args, "resume", False)),
    ).generate(
        paths,
        output,
        args.name,
    )
    report = validate_kit(output)
    print(format_report(report))
    if not report.ok:
        return 2
    print("\nNext steps:")
    print(f"  agentbridge serve {output}")
    print(f"  agentbridge mcp-config {output}")
    print(f"  agentbridge chat {output}")
    print(f"  agentbridge web {output} --port 8765")
    if args.target_base_url:
        print("\nWith HTTP execution:")
        print(f"  agentbridge doctor {output} --execute --base-url {args.target_base_url}")
        print(f"  agentbridge mcp-config {output} --base-url {args.target_base_url} --execute --bearer-env API_TOKEN")
        print(f"  agentbridge serve {output} --base-url {args.target_base_url} --execute --read-only")
    print(f"\nKit manifest: {json.dumps(kit.to_manifest(), sort_keys=True)}")
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
    if getattr(args, "bearer_env", None):
        token = os.environ.get(args.bearer_env)
        if not token:
            raise ValueError(f"Environment variable {args.bearer_env!r} is not set for --bearer-env")
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _client_config_from_args(args: argparse.Namespace) -> MCPClientConfig:
    return MCPClientConfig(
        kit_dir=Path(args.kit),
        server_name=args.server_name,
        command=args.command_path,
        base_url=args.base_url or "",
        headers=_header_items_from_args(args),
        bearer_token=getattr(args, "bearer_token", None) or "",
        bearer_env=getattr(args, "bearer_env", None) or "",
        execute=bool(getattr(args, "execute", False)),
        timeout=float(getattr(args, "timeout", 30.0)),
        read_only=bool(getattr(args, "read_only", False)),
        deny_risks=list(getattr(args, "deny_risk", None) or []),
        allow_tools=list(getattr(args, "allow_tool", None) or []),
        audit_log=Path(args.audit_log) if getattr(args, "audit_log", None) else None,
    )


def _header_items_from_args(args: argparse.Namespace) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in getattr(args, "header", None) or []:
        if "=" not in item:
            raise ValueError(f"Invalid --header value {item!r}; expected NAME=VALUE")
        key, value = item.split("=", 1)
        headers[key] = value
    return headers


def _run_mcp_config(args: argparse.Namespace) -> int:
    config = _client_config_from_args(args)
    configs = build_mcp_client_configs(config)
    if args.write:
        kit_dir = Path(args.kit)
        out = kit_dir / "clients" / "mcp-client-configs.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(configs, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Wrote {out}")
        return 0
    if args.json:
        print(json.dumps(configs, indent=2, sort_keys=True))
    else:
        print(format_mcp_client_configs(config))
    return 0


def _chat_config_from_args(args: argparse.Namespace) -> ChatConfig:
    return ChatConfig(
        kit_dir=Path(args.kit),
        base_url=getattr(args, "base_url", None) or "",
        headers=_headers_from_args(args),
        execute=bool(getattr(args, "execute", False)),
        timeout=float(getattr(args, "timeout", 30.0)),
        read_only=bool(getattr(args, "read_only", False)),
        deny_risks=set(getattr(args, "deny_risk", None) or []),
        allow_tools=set(getattr(args, "allow_tool", None) or []),
        audit_log=Path(args.audit_log) if getattr(args, "audit_log", None) else None,
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
        read_only=args.read_only,
        deny_risks=set(args.deny_risk or []),
        allow_tools=set(args.allow_tool or []),
        audit_log=Path(args.audit_log) if args.audit_log else None,
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
    parser.add_argument("--bearer-env", help="Read the bearer token from this environment variable at runtime.")
    parser.add_argument("--execute", action="store_true", help="Execute HTTP tools against --base-url. Without this, calls return dry-run plans only.")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds when --execute is enabled.")
    parser.add_argument("--read-only", action="store_true", help="Block write, destructive, and external-side-effect tools at runtime.")
    parser.add_argument("--deny-risk", action="append", choices=["read", "write", "destructive", "external_side_effect"], help="Disable a risk level at runtime. May be repeated.")
    parser.add_argument("--allow-tool", action="append", help="Allow only this tool name at runtime. May be repeated.")
    parser.add_argument("--audit-log", help="Write JSONL audit events for tool calls to this file.")


def _print_report(report: Any, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(format_report(report))


def _print_progress(message: str) -> None:
    print(f"agentbridge: {message}", file=sys.stderr)


def _build_ai_confirmation(args: argparse.Namespace) -> Any:
    threshold = int(getattr(args, "review_threshold", 100) or 0)
    if threshold <= 0 or getattr(args, "yes", False):
        return None
    if not sys.stdin.isatty():
        return None

    def _confirm(capabilities: list[Capability], kit_name: str, output_dir: Path) -> bool:
        if len(capabilities) < threshold:
            return True
        print(_format_capability_review(capabilities, kit_name, output_dir), file=sys.stderr)
        print("Continue with AI analysis? This may take several minutes. [Y/n] ", end="", file=sys.stderr)
        answer = sys.stdin.readline().strip().lower()
        return answer in {"", "y", "yes"}

    return _confirm


def _format_capability_review(capabilities: list[Capability], kit_name: str, output_dir: Path) -> str:
    domain_counts: dict[str, int] = {}
    action_counts: dict[str, int] = {}
    risk_counts: dict[str, int] = {}
    for cap in capabilities:
        domain_counts[cap.domain] = domain_counts.get(cap.domain, 0) + 1
        action_counts[cap.action] = action_counts.get(cap.action, 0) + 1
        risk_counts[cap.risk] = risk_counts.get(cap.risk, 0) + 1

    lines = [
        "",
        f"agentbridge: Large project review for kit {kit_name!r}",
        f"agentbridge: Output directory already created: {output_dir}",
        f"agentbridge: Candidate capabilities discovered: {len(capabilities)}",
        "agentbridge: Top domains:",
    ]
    for domain, count in sorted(domain_counts.items(), key=lambda item: (-item[1], item[0]))[:8]:
        lines.append(f"  - {domain}: {count}")
    lines.append("agentbridge: Top actions:")
    for action, count in sorted(action_counts.items(), key=lambda item: (-item[1], item[0]))[:8]:
        lines.append(f"  - {action}: {count}")
    lines.append("agentbridge: Risk summary:")
    for risk in ("read", "write", "destructive", "external_side_effect"):
        if risk in risk_counts:
            lines.append(f"  - {risk}: {risk_counts[risk]}")
    lines.append("agentbridge: Example capabilities:")
    for cap in sorted(capabilities, key=lambda item: (item.domain, item.resource, item.action, item.name))[:20]:
        lines.append(f"  - {cap.name} ({cap.domain}/{cap.action}, risk={cap.risk})")
    lines.append("agentbridge: Enter 'n' to skip AI analysis and finish a deterministic kit now.")
    return "\n".join(lines)


def _add_chat_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--user", default="local", help="User id used for chat memory and audit context.")
    parser.add_argument("--session", default="default", help="Session id used to load and save chat memory.")
    parser.add_argument("--memory-file", help="JSON file for chat memory. Defaults to <kit>/.agentbridge-chat-memory.json.")
    parser.add_argument("--no-memory", action="store_true", help="Disable chat memory persistence.")


def _add_llm_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--api-key", help="LLM API key. Defaults to ANTHROPIC_API_KEY env var.")
    parser.add_argument("--base-url", help="Custom LLM API endpoint. Defaults to ANTHROPIC_BASE_URL env var. Examples: https://api.deepseek.com/anthropic, https://openrouter.ai/api/v1")
    parser.add_argument("--model", help="LLM model name. Defaults to ANTHROPIC_MODEL env var or claude-sonnet-4-20250514. Examples: deepseek-v4-flash, claude-sonnet-4-20250514")
    parser.add_argument("--llm-timeout", type=float, help="LLM request timeout in seconds. Defaults to AGENTBRIDGE_LLM_TIMEOUT or 300.")
    parser.add_argument("--progress-interval", type=float, default=15.0, help="Seconds between AI wait heartbeat messages. Use 0 to disable.")
    parser.add_argument("--batch-size", "--ai-capability-limit", dest="batch_size", type=int, default=30, help="Maximum capabilities per AI batch. Use 0 for all at once.")
    parser.add_argument("--resume", action="store_true", help="Resume batch-enhanced generation from existing analysis state and batch files.")
    parser.add_argument("--review-threshold", type=int, default=100, help="Prompt before AI analysis when discovered capabilities reach this count. Use 0 to disable.")
    parser.add_argument("--yes", action="store_true", help="Skip interactive review prompts and continue with AI analysis.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentbridge",
        description="Generate Agent Integration Kits and run MCP/chat entrypoints for existing systems.",
        epilog=(
            "Examples:\n"
            "  agentbridge discover examples/writing_system\n"
            "  agentbridge init openapi.json -o .agentbridge/openapi-kit --no-ai\n"
            "  agentbridge serve .agentbridge/openapi-kit --base-url http://localhost:8080 --execute\n"
            "  agentbridge mcp-config .agentbridge/openapi-kit --base-url http://localhost:8080 --bearer-env API_TOKEN\n"
            "  agentbridge validate .agentbridge/openapi-kit\n"
            "  agentbridge chat .agentbridge/openapi-kit --read-only\n"
            "  agentbridge web .agentbridge/openapi-kit --port 8765\n\n"
            "Use 'agentbridge <command> --help' for command-specific options."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    discover = subparsers.add_parser("discover", help="Discover system capabilities (no API key needed).")
    discover.add_argument("paths", nargs="+", help="Files or directories to inspect.")

    generate = subparsers.add_parser("generate", help="Generate an Agent Integration Kit. Uses AI when an API key is configured.")
    generate.add_argument("paths", nargs="+", help="Files or directories to inspect.")
    generate.add_argument("--output", "-o", required=True, help="Output directory for the generated kit.")
    generate.add_argument("--name", help="Kit name. Defaults to the input directory name.")
    generate.add_argument("--no-ai", action="store_true", help="Generate deterministically without LLM enrichment; intended for schema-only kits such as OpenAPI-to-MCP.")
    _add_llm_options(generate)

    init = subparsers.add_parser("init", help="Generate, validate, and print next steps for a new AgentBridge kit.")
    init.add_argument("paths", nargs="+", help="Files or directories to inspect.")
    init.add_argument("--output", "-o", required=True, help="Output directory for the generated kit.")
    init.add_argument("--name", help="Kit name. Defaults to the input directory name.")
    init.add_argument("--no-ai", action="store_true", help="Generate deterministically without LLM enrichment; intended for schema-only kits such as OpenAPI-to-MCP.")
    init.add_argument("--target-base-url", help="Optional target system base URL to include in suggested next steps.")
    _add_llm_options(init)

    dry = subparsers.add_parser("dry-run", help="Dry-run a generated tool invocation.")
    dry.add_argument("kit", help="Generated kit directory.")
    dry.add_argument("tool", help="Tool name.")
    dry.add_argument("--args", default="{}", help="JSON arguments for the tool.")
    dry.add_argument("--confirmed", action="store_true", help="Mark high-risk operation as human-confirmed.")

    validate = subparsers.add_parser("validate", help="Validate a generated kit before connecting it to agents.")
    validate.add_argument("kit", help="Generated kit directory.")
    validate.add_argument("--json", action="store_true", help="Print machine-readable JSON report.")

    doctor = subparsers.add_parser("doctor", help="Diagnose kit readiness and runtime safety configuration.")
    doctor.add_argument("kit", help="Generated kit directory.")
    doctor.add_argument("--base-url", help="Target system base URL to check for execute mode.")
    doctor.add_argument("--execute", action="store_true", help="Check readiness for real HTTP execution.")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable JSON report.")

    mcp_config = subparsers.add_parser("mcp-config", help="Print or write MCP client config snippets for a generated kit.")
    mcp_config.add_argument("kit", help="Generated kit directory.")
    mcp_config.add_argument("--server-name", default="agentbridge", help="MCP server name to use in client config.")
    mcp_config.add_argument("--command-path", default="agentbridge", help="Command clients should execute. Defaults to agentbridge.")
    mcp_config.add_argument("--json", action="store_true", help="Print all snippets as JSON.")
    mcp_config.add_argument("--write", action="store_true", help="Write snippets to <kit>/clients/mcp-client-configs.json.")
    _add_runtime_options(mcp_config)

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
