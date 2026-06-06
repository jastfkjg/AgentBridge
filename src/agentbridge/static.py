from __future__ import annotations

from pathlib import Path
from typing import Any

from agentbridge.agent import AIGenerator
from agentbridge.models import Capability
from agentbridge.policy import risk_reason


class StaticAIGenerator(AIGenerator):
    """Deterministic kit enhancer used when no LLM backend is configured."""

    def __init__(self) -> None:
        self.api_key = ""
        self.base_url = ""
        self.model = "static"
        self._backend = "static"

    def generate_all(
        self,
        capabilities: list[Capability],
        kit_name: str,
        input_paths: list[Path] | None = None,
    ) -> dict[str, Any]:
        domains = sorted({cap.domain for cap in capabilities})
        return {
            "enhanced_capabilities": capabilities,
            "rule_signals": {
                "candidate_capabilities": [cap.to_dict() for cap in capabilities],
                "risk_policy": {
                    "read": {"confirm_required": False},
                    "write": {"confirm_required": False},
                    "destructive": {"confirm_required": True},
                    "external_side_effect": {"confirm_required": True},
                },
            },
            "agent_analysis": {
                "summary": f"Static analysis kit for {kit_name}.",
                "business_objects": [
                    {
                        "name": domain,
                        "description": f"Capabilities discovered for the {domain} domain.",
                        "evidence": sorted({cap.source.kind for cap in capabilities if cap.domain == domain}),
                    }
                    for domain in domains
                ],
                "workflows": [],
                "permission_boundaries": ["Authentication and authorization must be provided by the target system."],
                "side_effects": [
                    {"tool": cap.name, "risk": cap.risk, "reason": risk_reason(cap.risk)}
                    for cap in capabilities
                    if cap.risk in {"destructive", "external_side_effect"}
                ],
                "assumptions": ["Generated without LLM enrichment; descriptions and schemas come from deterministic discovery."],
            },
            "system_prompt": build_static_system_prompt(kit_name, capabilities),
            "skills": build_static_skills(capabilities),
        }


def build_static_system_prompt(kit_name: str, capabilities: list[Capability]) -> str:
    lines = [
        f"# {kit_name} Assistant",
        "",
        "You operate an existing system through AgentBridge-generated tools.",
        "Validate tool arguments before calling tools. Use dry-run output to explain planned changes.",
        "Do not perform destructive or external-side-effect operations unless the user explicitly confirms them.",
        "",
        "## Tools",
    ]
    for cap in sorted(capabilities, key=lambda item: item.name):
        lines.append(f"- `{cap.name}`: {cap.description} Risk: `{cap.risk}`.")
    return "\n".join(lines) + "\n"


def build_static_skills(capabilities: list[Capability]) -> dict[str, str]:
    skills: dict[str, list[str]] = {}
    for cap in sorted(capabilities, key=lambda item: item.name):
        lines = skills.setdefault(
            cap.domain,
            [
                f"# {cap.domain.title()} Skill",
                "",
                "Use this skill when the user asks to inspect or operate this domain.",
                "",
                "## Available Tools",
            ],
        )
        lines.append(f"- `{cap.name}`: {cap.description} Risk: `{cap.risk}`.")
    return {domain: "\n".join(lines) + "\n" for domain, lines in skills.items()}
