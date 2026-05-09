from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentbridge.discovery import CapabilityDiscoverer
from agentbridge.generator import AgentKitGenerator
from agentbridge.runtime import DryRunError, dry_run


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
            kit = AgentKitGenerator().generate(paths, Path(args.output), args.name)
            print(json.dumps(kit.to_manifest(), indent=2, sort_keys=True))
            return 0
        if args.command == "dry-run":
            result = dry_run(Path(args.kit), args.tool, json.loads(args.args), confirmed=args.confirmed)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["allowed"] or result["requires_confirmation"] else 2
    except (OSError, json.JSONDecodeError, DryRunError, ValueError) as exc:
        print(f"agentbridge: {exc}", file=sys.stderr)
        return 1
    parser.print_help()
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentbridge", description="Generate Agent Integration Kits for existing systems.")
    subparsers = parser.add_subparsers(dest="command")

    discover = subparsers.add_parser("discover", help="Discover system capabilities.")
    discover.add_argument("paths", nargs="+", help="Files or directories to inspect.")

    generate = subparsers.add_parser("generate", help="Generate an Agent Integration Kit.")
    generate.add_argument("paths", nargs="+", help="Files or directories to inspect.")
    generate.add_argument("--output", "-o", required=True, help="Output directory for the generated kit.")
    generate.add_argument("--name", help="Kit name. Defaults to the input directory name.")

    dry = subparsers.add_parser("dry-run", help="Dry-run a generated tool invocation.")
    dry.add_argument("kit", help="Generated kit directory.")
    dry.add_argument("tool", help="Tool name.")
    dry.add_argument("--args", default="{}", help="JSON arguments for the tool.")
    dry.add_argument("--confirmed", action="store_true", help="Mark high-risk operation as human-confirmed.")

    return parser


if __name__ == "__main__":
    raise SystemExit(main())

