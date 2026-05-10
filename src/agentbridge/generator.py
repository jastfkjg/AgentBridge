from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from agentbridge.agent import AIGenerator
from agentbridge.discovery import CapabilityDiscoverer
from agentbridge.io import write_json, write_text
from agentbridge.models import Capability, IntegrationKit
from agentbridge.naming import humanize
from agentbridge.policy import risk_reason

logger = logging.getLogger(__name__)


class AgentKitGenerator:
    def __init__(
        self,
        ai_generator: AIGenerator,
        discoverer: CapabilityDiscoverer | None = None,
    ) -> None:
        if not isinstance(ai_generator, AIGenerator):
            raise TypeError(
                "ai_generator is required and must be an AIGenerator instance. "
                "LLM-based generation is mandatory — configure an API key to proceed."
            )
        self.ai_generator = ai_generator
        self.discoverer = discoverer or CapabilityDiscoverer()

    def generate(self, input_paths: list[Path], output_dir: Path, name: str | None = None) -> IntegrationKit:
        capabilities = self.discoverer.discover(input_paths)
        kit_name = name or infer_kit_name(input_paths)

        ai_result = self.ai_generator.generate_all(capabilities, kit_name, input_paths=input_paths)
        capabilities = ai_result["enhanced_capabilities"]

        kit = IntegrationKit(name=kit_name, capabilities=capabilities, output_dir=str(output_dir))
        output_dir.mkdir(parents=True, exist_ok=True)

        write_json(output_dir / "manifest.json", kit.to_manifest())
        write_json(output_dir / "capabilities.json", [cap.to_dict() for cap in capabilities])
        write_json(output_dir / "tools" / "mcp_tools.json", build_mcp_tools(capabilities))
        write_json(output_dir / "tools" / "openai_tools.json", build_openai_tools(capabilities))
        write_json(output_dir / "tools" / "claude_tools.json", build_claude_tools(capabilities))
        write_text(output_dir / "tools" / "vercel_ai_tools.ts", build_vercel_tools(capabilities))
        write_json(output_dir / "resources" / "schema.json", build_resource_schema(capabilities))
        write_json(output_dir / "guardrails" / "permissions.json", build_guardrails(capabilities))
        write_json(output_dir / "tests" / "tool_invocation_tests.json", build_invocation_tests(capabilities))
        write_text(output_dir / "tests" / "test_generated_tools.py", build_generated_test_file())
        write_json(output_dir / "dry_run_plan.json", build_dry_run_plan(capabilities))

        system_prompt = ai_result.get("system_prompt", "")
        if system_prompt:
            write_text(output_dir / "prompts" / "system.md", system_prompt)

        skills = ai_result.get("skills", {})
        for domain, content in skills.items():
            if content:
                write_text(output_dir / "skills" / f"{domain}.md", content)

        return kit


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
