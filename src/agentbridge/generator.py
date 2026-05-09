from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, TYPE_CHECKING

from agentbridge.discovery import CapabilityDiscoverer
from agentbridge.io import write_json, write_text
from agentbridge.models import Capability, IntegrationKit
from agentbridge.naming import humanize
from agentbridge.policy import risk_reason

if TYPE_CHECKING:
    from agentbridge.agent import AIGenerator

logger = logging.getLogger(__name__)


class AgentKitGenerator:
    def __init__(
        self,
        discoverer: CapabilityDiscoverer | None = None,
        ai_generator: AIGenerator | None = None,
    ) -> None:
        self.discoverer = discoverer or CapabilityDiscoverer()
        self.ai_generator = ai_generator

    def generate(self, input_paths: list[Path], output_dir: Path, name: str | None = None) -> IntegrationKit:
        capabilities = self.discoverer.discover(input_paths)
        kit_name = name or infer_kit_name(input_paths)

        if self.ai_generator:
            capabilities = self._ai_enhance_capabilities(capabilities, kit_name)

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

        if self.ai_generator:
            self._ai_write_prompts_and_skills(output_dir, kit_name, capabilities)
        else:
            write_text(output_dir / "prompts" / "system.md", build_system_prompt(kit_name, capabilities))
            for domain, domain_caps in group_by_domain(capabilities).items():
                write_text(output_dir / "skills" / f"{domain}.md", build_skill(domain, domain_caps))

        return kit

    def _ai_enhance_capabilities(
        self, capabilities: list[Capability], kit_name: str
    ) -> list[Capability]:
        try:
            enhancements = self.ai_generator.enhance_tools(capabilities)
            for cap in capabilities:
                enh = enhancements.get(cap.name, {})
                if enh.get("description"):
                    cap.description = enh["description"]
                if enh.get("caveats"):
                    cap.description = f"{cap.description} Caveats: {enh['caveats']}"
        except Exception as exc:
            logger.warning("AI tool enhancement failed, using deterministic descriptions: %s", exc)

        try:
            risk_enhancements = self.ai_generator.enhance_risk_assessment(capabilities)
            for cap in capabilities:
                risk_info = risk_enhancements.get(cap.name, {})
                if risk_info.get("reason"):
                    pass
        except Exception as exc:
            logger.warning("AI risk enhancement failed, using deterministic risk levels: %s", exc)

        try:
            additional = self.ai_generator.infer_additional_tools(capabilities)
            for tool_def in additional:
                if isinstance(tool_def, dict) and tool_def.get("name"):
                    from agentbridge.models import SourceRef

                    cap = Capability(
                        name=tool_def["name"],
                        domain=tool_def.get("domain", "inferred"),
                        resource=tool_def.get("resource", "inferred"),
                        action=tool_def.get("action", "run"),
                        description=tool_def.get("description", tool_def["name"]),
                        input_schema=tool_def.get("input_schema", {"type": "object", "properties": {}}),
                        risk=tool_def.get("risk", "read"),
                        confirm_required=tool_def.get("risk", "read") in {"destructive", "external_side_effect"},
                        source=SourceRef("ai_inferred", "", tool_def.get("rationale", "")),
                        transport={"type": "inferred"},
                        dry_run_supported=True,
                    )
                    capabilities.append(cap)
        except Exception as exc:
            logger.warning("AI tool inference failed: %s", exc)

        return capabilities

    def _ai_write_prompts_and_skills(
        self, output_dir: Path, kit_name: str, capabilities: list[Capability]
    ) -> None:
        try:
            ai_prompt = self.ai_generator.generate_system_prompt(capabilities, kit_name)
            write_text(output_dir / "prompts" / "system.md", ai_prompt)
        except Exception as exc:
            logger.warning("AI system prompt generation failed, using deterministic prompt: %s", exc)
            write_text(output_dir / "prompts" / "system.md", build_system_prompt(kit_name, capabilities))

        try:
            ai_skills = self.ai_generator.generate_skills(capabilities, kit_name)
            for domain, content in ai_skills.items():
                if content:
                    write_text(output_dir / "skills" / f"{domain}.md", content)
        except Exception as exc:
            logger.warning("AI skill generation failed, using deterministic skills: %s", exc)
            for domain, domain_caps in group_by_domain(capabilities).items():
                write_text(output_dir / "skills" / f"{domain}.md", build_skill(domain, domain_caps))


def build_mcp_tools(capabilities: list[Capability]) -> dict[str, Any]:
    return {
        "version": "2024-11-05",
        "tools": [
            {
                "name": cap.name,
                "description": tool_description(cap),
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
                "description": tool_description(cap),
                "parameters": cap.input_schema,
            },
        }
        for cap in capabilities
    ]


def build_claude_tools(capabilities: list[Capability]) -> list[dict[str, Any]]:
    return [
        {
            "name": cap.name,
            "description": tool_description(cap),
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


def build_system_prompt(name: str, capabilities: list[Capability]) -> str:
    lines = [
        f"# {name} Agent System Prompt",
        "",
        "You are an AI assistant integrated through AgentBridge.",
        "Use generated tools to operate the host system. Prefer dry-run before mutations.",
        "",
        "Safety rules:",
        "- Read operations may run directly when the user intent is clear.",
        "- Write operations should summarize the planned change before execution.",
        "- Destructive and external-side-effect operations require explicit human confirmation.",
        "- Never bypass guardrails, permission checks, or dry-run validation.",
        "- If tool arguments are ambiguous, ask for the missing required fields.",
        "",
        "Available capabilities:",
    ]
    for cap in capabilities:
        confirm = "requires confirmation" if cap.confirm_required else "no confirmation required"
        lines.append(f"- `{cap.name}`: {cap.description} ({cap.risk}, {confirm})")
    lines.append("")
    return "\n".join(lines)


def build_skill(domain: str, capabilities: list[Capability]) -> str:
    resources = sorted({cap.resource for cap in capabilities})
    lines = [
        f"# {humanize(domain).title()} Skill",
        "",
        f"Use this skill when the user asks to operate the {humanize(domain)} domain.",
        "",
        "## Resources",
    ]
    for resource in resources:
        actions = sorted({cap.action for cap in capabilities if cap.resource == resource})
        lines.append(f"- `{resource}`: {', '.join(actions)}")
    lines.extend(["", "## Workflow", ""])
    lines.extend(
        [
            "1. Identify the target resource and action from the user's request.",
            "2. Collect required fields from the selected tool input schema.",
            "3. Run dry-run validation for write, destructive, and external-side-effect actions.",
            "4. For destructive actions, check references or dependent resources before asking for confirmation.",
            "5. For content rewrite or generation actions, preserve user-provided constraints and domain facts.",
            "6. Execute only after guardrails allow the operation.",
            "7. Summarize the completed system change with tool result identifiers.",
        ]
    )
    lines.extend(["", "## Tools", ""])
    for cap in capabilities:
        lines.append(f"- `{cap.name}`: {cap.description}. Risk: `{cap.risk}`.")
    lines.append("")
    return "\n".join(lines)


def tool_description(cap: Capability) -> str:
    suffix = " Requires explicit human confirmation." if cap.confirm_required else ""
    return f"{cap.description}. Risk level: {cap.risk}.{suffix}"


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

