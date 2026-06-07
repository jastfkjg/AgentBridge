from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from agentbridge.agent import AIGenerator
from agentbridge.client_config import MCPClientConfig, build_clients_readme, build_mcp_client_configs
from agentbridge.discovery import CapabilityDiscoverer
from agentbridge.io import write_json, write_text
from agentbridge.models import Capability, IntegrationKit, KIT_PROTOCOL_VERSION
from agentbridge.naming import humanize
from agentbridge.policy import risk_reason

logger = logging.getLogger(__name__)


class GenerationBoundaryError(ValueError):
    pass


class AgentKitGenerator:
    def __init__(
        self,
        ai_generator: AIGenerator,
        discoverer: CapabilityDiscoverer | None = None,
        progress: Callable[[str], None] | None = None,
        confirm_ai_analysis: Callable[[list[Capability], str, Path], bool] | None = None,
    ) -> None:
        if not isinstance(ai_generator, AIGenerator):
            raise TypeError(
                "ai_generator is required and must be an AIGenerator instance. "
                "LLM-based generation is mandatory — configure an API key to proceed."
            )
        self.ai_generator = ai_generator
        self.discoverer = discoverer or CapabilityDiscoverer()
        self.progress = progress
        self.confirm_ai_analysis = confirm_ai_analysis

    def generate(self, input_paths: list[Path], output_dir: Path, name: str | None = None) -> IntegrationKit:
        self._progress("Checking output boundary...")
        validate_output_boundary(input_paths, output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        self._write_status(output_dir, "discovering", "Discovering candidate capabilities.")
        self._progress("Discovering candidate capabilities...")
        rule_capabilities = self.discoverer.discover(input_paths)
        kit_name = name or infer_kit_name(input_paths)
        self._progress(f"Discovered {len(rule_capabilities)} candidate capabilities for kit {kit_name!r}.")
        write_json(output_dir / "analysis" / "rule_signals.json", {
            "candidate_capabilities": [cap.to_dict() for cap in rule_capabilities],
            "status": "discovered",
        })
        self._write_status(
            output_dir,
            "analyzing",
            "Candidate discovery complete. Waiting for LLM project analysis.",
            kit_name=kit_name,
            candidate_capability_count=len(rule_capabilities),
        )

        analysis_generator = self.ai_generator
        if self._should_offer_ai_confirmation(analysis_generator):
            if not self.confirm_ai_analysis(rule_capabilities, kit_name, output_dir):
                from agentbridge.static import StaticAIGenerator

                self._progress("Skipping AI analysis. Generating deterministic kit metadata...")
                self._write_status(
                    output_dir,
                    "skipped_ai",
                    "User skipped AI analysis after reviewing discovered capabilities.",
                    kit_name=kit_name,
                    candidate_capability_count=len(rule_capabilities),
                )
                analysis_generator = StaticAIGenerator()

        model = getattr(analysis_generator, "model", "")
        if model == "static":
            self._progress("Generating deterministic kit metadata...")
        else:
            model_note = f" using {model}" if model else ""
            self._progress(f"Running AI project analysis{model_note}; this can take a minute...")
        try:
            ai_result = analysis_generator.generate_all(rule_capabilities, kit_name, input_paths=input_paths)
        except Exception as exc:
            self._write_status(
                output_dir,
                "failed",
                str(exc),
                kit_name=kit_name,
                candidate_capability_count=len(rule_capabilities),
            )
            raise
        capabilities = ai_result["enhanced_capabilities"]
        self._progress(f"Analysis complete. Writing {len(capabilities)} capabilities to {output_dir}...")

        kit = IntegrationKit(name=kit_name, capabilities=capabilities, output_dir=str(output_dir))
        self._write_status(
            output_dir,
            "writing",
            "Analysis complete. Writing kit files.",
            kit_name=kit_name,
            candidate_capability_count=len(rule_capabilities),
            capability_count=len(capabilities),
        )

        write_json(output_dir / "manifest.json", kit.to_manifest())
        write_json(output_dir / "capabilities.json", [cap.to_dict() for cap in capabilities])
        write_json(output_dir / "analysis" / "rule_signals.json", ai_result.get("rule_signals", {
            "candidate_capabilities": [cap.to_dict() for cap in rule_capabilities],
        }))
        write_json(output_dir / "analysis" / "agent_analysis.json", ai_result.get("agent_analysis", {}))
        write_text(output_dir / "spec" / "kit-protocol.md", build_kit_protocol_doc())
        write_json(output_dir / "tools" / "mcp_tools.json", build_mcp_tools(capabilities))
        write_json(output_dir / "tools" / "openai_tools.json", build_openai_tools(capabilities))
        write_json(output_dir / "tools" / "claude_tools.json", build_claude_tools(capabilities))
        write_text(output_dir / "tools" / "vercel_ai_tools.ts", build_vercel_tools(capabilities))
        write_json(output_dir / "resources" / "schema.json", build_resource_schema(capabilities))
        write_json(output_dir / "guardrails" / "permissions.json", build_guardrails(capabilities))
        write_json(output_dir / "tests" / "tool_invocation_tests.json", build_invocation_tests(capabilities))
        write_text(output_dir / "tests" / "test_generated_tools.py", build_generated_test_file())
        write_json(output_dir / "dry_run_plan.json", build_dry_run_plan(capabilities))
        client_config = MCPClientConfig(kit_dir=output_dir, server_name=kit_name)
        write_json(output_dir / "clients" / "mcp-client-configs.json", build_mcp_client_configs(client_config))
        write_text(output_dir / "clients" / "README.md", build_clients_readme(kit_name, client_config))

        system_prompt = ai_result.get("system_prompt", "")
        if system_prompt:
            write_text(output_dir / "prompts" / "system.md", system_prompt)

        skills = ai_result.get("skills", {})
        for domain, content in skills.items():
            if content:
                write_text(output_dir / "skills" / f"{domain}.md", content)

        self._write_status(
            output_dir,
            "complete",
            "Generated AgentBridge kit.",
            kit_name=kit_name,
            candidate_capability_count=len(rule_capabilities),
            capability_count=len(capabilities),
        )
        self._progress(f"Generated AgentBridge kit at {output_dir}.")
        return kit

    def _progress(self, message: str) -> None:
        if self.progress:
            self.progress(message)

    def _should_offer_ai_confirmation(self, ai_generator: AIGenerator) -> bool:
        return bool(self.confirm_ai_analysis and getattr(ai_generator, "model", "") != "static")

    def _write_status(self, output_dir: Path, status: str, message: str, **extra: Any) -> None:
        write_json(output_dir / "generation_status.json", {
            "status": status,
            "message": message,
            **extra,
        })


def build_kit_protocol_doc() -> str:
    return f"""# AgentBridge Kit Protocol

Protocol: `{KIT_PROTOCOL_VERSION}`

An AgentBridge kit is a stable, versioned directory that can be consumed by MCP servers, Claude Agent SDK, OpenAI tool callers, Vercel AI SDK applications, CI checks, and local dry-run tools.

## Required Files

- `manifest.json`: protocol version, kit metadata, risk summary, and output paths.
- `capabilities.json`: normalized business capabilities after AI agent analysis and enhancement.
- `analysis/rule_signals.json`: deterministic scanner output used as candidate evidence.
- `analysis/agent_analysis.json`: AI agent project analysis, assumptions, workflows, side effects, and risk reasoning.
- `tools/mcp_tools.json`: MCP tool definitions.
- `tools/openai_tools.json`: OpenAI function/tool definitions.
- `tools/claude_tools.json`: Claude tool definitions.
- `tools/vercel_ai_tools.ts`: Vercel AI SDK tool stubs.
- `skills/*.md`: domain workflows for agent behavior.
- `prompts/system.md`: system prompt for the integrated assistant.
- `resources/schema.json`: normalized resource/action schema.
- `guardrails/permissions.json`: risk policy and confirmation rules.
- `tests/tool_invocation_tests.json`: generated invocation contracts.
- `tests/test_generated_tools.py`: executable kit contract tests.
- `dry_run_plan.json`: no-side-effect execution plan for each tool.
- `clients/mcp-client-configs.json`: ready-to-use MCP client snippets.
- `clients/README.md`: client setup and safe runtime guidance.

## Compatibility

Consumers should read `manifest.json` first, verify `protocol`, then resolve files through `outputs`. New optional files may be added without breaking this version. Required file names are stable for `agentbridge-kit/v1`.

## Safety Contract

Generated tools must not execute destructive or external-side-effect operations unless `guardrails/permissions.json` marks the call as confirmed by a human. Dry-run consumers must return planned calls only.

## Project Boundary

AgentBridge must not modify the target project during discovery or generation. All generated artifacts are written under the caller-provided output directory. The output directory should be outside the scanned project unless it is an ignored integration directory such as `.agentbridge`.
"""


def validate_output_boundary(input_paths: list[Path], output_dir: Path) -> None:
    output = output_dir.resolve()
    for input_path in input_paths:
        target = input_path.resolve()
        root = target.parent if target.is_file() else target
        if output == root:
            raise GenerationBoundaryError("Output directory must not be the target project root.")
        if is_relative_to(output, root) and not is_allowed_project_output(output, root):
            raise GenerationBoundaryError(
                "Output directory is inside the target project. "
                "Use a dedicated ignored directory such as <project>/.agentbridge/<kit> "
                "or choose a path outside the project."
            )


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def is_allowed_project_output(output: Path, project_root: Path) -> bool:
    try:
        relative = output.relative_to(project_root)
    except ValueError:
        return False
    parts = relative.parts
    return bool(parts) and parts[0] in {".agentbridge", "agentbridge-kit"}


def build_mcp_tools(capabilities: list[Capability]) -> dict[str, Any]:
    return {
        "version": "2024-11-05",
        "tools": [
            {
                "name": cap.name,
                "description": cap.description,
                "inputSchema": cap.input_schema,
                "annotations": {
                    "risk": cap.risk,
                    "confirm_required": cap.confirm_required,
                    "dry_run_supported": cap.dry_run_supported,
                    "resource": cap.resource,
                    "domain": cap.domain,
                },
            }
            for cap in capabilities
        ],
    }


def build_openai_tools(capabilities: list[Capability]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": cap.name,
                "description": cap.description,
                "parameters": cap.input_schema,
            },
        }
        for cap in capabilities
    ]


def build_claude_tools(capabilities: list[Capability]) -> list[dict[str, Any]]:
    return [
        {
            "name": cap.name,
            "description": cap.description,
            "input_schema": cap.input_schema,
        }
        for cap in capabilities
    ]


def build_vercel_tools(capabilities: list[Capability]) -> str:
    lines = [
        "import { tool } from 'ai';",
        "import { z } from 'zod';",
        "",
        "export const generatedTools = {",
    ]
    for cap in capabilities:
        lines.extend(
            [
                f"  {cap.name}: tool({{",
                f"    description: {cap.description!r},",
                "    parameters: z.record(z.any()),",
                "    execute: async (args) => {",
                f"      return {{ dryRun: true, tool: {cap.name!r}, args }};",
                "    },",
                "  }),",
            ]
        )
    lines.append("};")
    lines.append("")
    return "\n".join(lines)


def build_resource_schema(capabilities: list[Capability]) -> dict[str, Any]:
    resources: dict[str, Any] = {}
    for cap in capabilities:
        item = resources.setdefault(
            cap.resource,
            {
                "domain": cap.domain,
                "actions": [],
                "properties": {},
                "sources": [],
            },
        )
        item["actions"].append(cap.action)
        item["properties"].update(cap.input_schema.get("properties", {}))
        item["sources"].append(cap.source.to_dict())
    for resource in resources.values():
        resource["actions"] = sorted(set(resource["actions"]))
    return {"resources": resources}


def build_guardrails(capabilities: list[Capability]) -> dict[str, Any]:
    return {
        "default_mode": "dry_run_first",
        "risk_policy": {
            "read": {"confirm_required": False, "allow_dry_run": True},
            "write": {"confirm_required": False, "allow_dry_run": True},
            "destructive": {"confirm_required": True, "allow_dry_run": True},
            "external_side_effect": {"confirm_required": True, "allow_dry_run": True},
        },
        "tools": {
            cap.name: {
                "risk": cap.risk,
                "confirm_required": cap.confirm_required,
                "dry_run_supported": cap.dry_run_supported,
                "reason": risk_reason(cap.risk),
                "resource": cap.resource,
                "action": cap.action,
                "transport": cap.transport,
            }
            for cap in capabilities
        },
    }


def build_invocation_tests(capabilities: list[Capability]) -> list[dict[str, Any]]:
    tests: list[dict[str, Any]] = []
    for cap in capabilities:
        args = sample_args(cap.input_schema)
        tests.append(
            {
                "name": f"{cap.name}_schema_and_guardrail",
                "tool": cap.name,
                "args": args,
                "expect": {
                    "risk": cap.risk,
                    "confirm_required": cap.confirm_required,
                    "dry_run_allowed": True,
                },
            }
        )
    return tests


def build_generated_test_file() -> str:
    return '''import json
import unittest
from pathlib import Path


class GeneratedToolContractTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.tools = json.loads((self.root / "tools" / "mcp_tools.json").read_text())
        self.guardrails = json.loads((self.root / "guardrails" / "permissions.json").read_text())
        self.invocations = json.loads((self.root / "tests" / "tool_invocation_tests.json").read_text())

    def test_every_tool_has_guardrail(self):
        guardrail_tools = self.guardrails["tools"]
        for tool in self.tools["tools"]:
            self.assertIn(tool["name"], guardrail_tools)

    def test_high_risk_tools_require_confirmation(self):
        for name, rule in self.guardrails["tools"].items():
            if rule["risk"] in {"destructive", "external_side_effect"}:
                self.assertTrue(rule["confirm_required"], name)

    def test_invocation_tests_reference_existing_tools(self):
        names = {tool["name"] for tool in self.tools["tools"]}
        for invocation in self.invocations:
            self.assertIn(invocation["tool"], names)


if __name__ == "__main__":
    unittest.main()
'''


def build_dry_run_plan(capabilities: list[Capability]) -> dict[str, Any]:
    return {
        "mode": "no_side_effects",
        "steps": [
            "Load requested tool definition.",
            "Validate arguments against the generated input schema.",
            "Evaluate permission guardrail and risk level.",
            "Return planned transport call without executing it.",
            "Require explicit human confirmation for destructive and external-side-effect operations.",
        ],
        "tools": {
            cap.name: {
                "transport": cap.transport,
                "risk": cap.risk,
                "confirm_required": cap.confirm_required,
                "sample_args": sample_args(cap.input_schema),
            }
            for cap in capabilities
        },
    }


def sample_args(schema: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in schema.get("properties", {}).items():
        typ = value.get("type", "string") if isinstance(value, dict) else "string"
        if typ == "number" or typ == "integer":
            result[key] = 1
        elif typ == "boolean":
            result[key] = True
        elif typ == "array":
            result[key] = []
        elif typ == "object":
            result[key] = {}
        else:
            result[key] = f"sample_{key}"
    return result


def group_by_domain(capabilities: list[Capability]) -> dict[str, list[Capability]]:
    grouped: dict[str, list[Capability]] = defaultdict(list)
    for cap in capabilities:
        grouped[cap.domain].append(cap)
    return dict(sorted(grouped.items()))


def infer_kit_name(paths: list[Path]) -> str:
    if not paths:
        return "agentbridge-kit"
    first = paths[0]
    if first.is_file():
        return f"{first.stem}-agent-kit"
    return f"{first.name}-agent-kit"
