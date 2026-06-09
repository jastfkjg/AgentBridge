from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

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


class LocalProjectAIGenerator(StaticAIGenerator):
    """Local basic project analyzer used when agentic AI is unavailable or times out."""

    def __init__(
        self,
        reason: str = "",
        progress: Callable[[str], None] | None = None,
        timeout: float = 0.0,
    ) -> None:
        self.api_key = ""
        self.base_url = ""
        self.model = "local-basic-agentic"
        self._backend = "local-basic"
        self.analysis_mode = "agentic"
        self.reason = reason
        self.progress = progress
        self.timeout = timeout
        self.agent_plan_timeout = timeout
        self.agent_batch_timeout = timeout
        self.agentic_guidance = ""

    def set_progress(self, progress: Callable[[str], None] | None) -> None:
        self.progress = progress

    def set_agentic_guidance(self, guidance: str) -> None:
        self.agentic_guidance = guidance

    def uses_agentic_analysis(self, input_paths: list[Path] | None = None) -> bool:
        return True

    def plan_agentic_analysis(
        self,
        capabilities: list[Capability],
        kit_name: str,
        input_paths: list[Path],
    ) -> dict[str, Any]:
        if self.progress:
            self.progress("Running local basic project understanding from discovered capabilities and project files.")
        summary = _local_project_summary(capabilities, input_paths)
        return {
            "status": "local_basic",
            "project_summary": summary,
            "main_capability_names": [cap.name for cap in _rank_local_capabilities(capabilities)[: min(30, len(capabilities))]],
            "remaining_strategy": "Enhance remaining capabilities in scanner-ranked batches using local project evidence.",
            "questions": [],
            "notes_for_generation": self.reason,
        }

    def generate_all(
        self,
        capabilities: list[Capability],
        kit_name: str,
        input_paths: list[Path] | None = None,
    ) -> dict[str, Any]:
        if self.progress:
            self.progress(
                f"Running local basic project analysis for {len(capabilities)} candidate capabilities."
            )
        result = super().generate_all(capabilities, kit_name, input_paths=input_paths)
        result["agent_analysis"] = build_local_agent_analysis(
            kit_name,
            capabilities,
            input_paths or [],
            self.reason,
        )
        result["system_prompt"] = build_local_system_prompt(kit_name, capabilities, input_paths or [])
        result["skills"] = build_local_skills(capabilities)
        result["rule_signals"]["local_basic_analysis"] = {
            "enabled": True,
            "reason": self.reason,
            "input_paths": [str(path) for path in input_paths or []],
        }
        return result


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


def build_local_agent_analysis(
    kit_name: str,
    capabilities: list[Capability],
    input_paths: list[Path],
    reason: str = "",
) -> dict[str, Any]:
    domains = sorted({cap.domain for cap in capabilities})
    action_counts = Counter(cap.action for cap in capabilities)
    risk_counts = Counter(cap.risk for cap in capabilities)
    by_domain: dict[str, list[Capability]] = defaultdict(list)
    for cap in capabilities:
        by_domain[cap.domain].append(cap)

    workflows = []
    for domain, caps in sorted(by_domain.items(), key=lambda item: (-len(item[1]), item[0]))[:12]:
        actions = sorted({cap.action for cap in caps})
        workflows.append(
            {
                "name": f"{domain} operations",
                "description": f"Operate {domain} resources through {', '.join(actions[:8])} capabilities.",
                "tools": [cap.name for cap in _rank_local_capabilities(caps)[:10]],
            }
        )

    assumptions = [
        "Generated with local basic project analysis from deterministic discovery and project file hints.",
        "Use --resume with a configured AI provider to replace local-basic batches with deeper AI enhancement.",
    ]
    if reason:
        assumptions.append(reason)

    return {
        "summary": _local_project_summary(capabilities, input_paths, kit_name=kit_name),
        "business_objects": [
            {
                "name": domain,
                "description": _domain_description(domain, caps),
                "evidence": sorted({cap.source.kind for cap in caps}),
                "capability_count": len(caps),
                "top_actions": dict(Counter(cap.action for cap in caps).most_common(6)),
            }
            for domain, caps in sorted(by_domain.items(), key=lambda item: (-len(item[1]), item[0]))
        ],
        "workflows": workflows,
        "permission_boundaries": [
            "Authentication, authorization, tenancy, and rate limits must be enforced by the target system.",
            "Generated write, destructive, and external-side-effect tools must respect AgentBridge guardrails and dry-run review.",
        ],
        "side_effects": [
            {"tool": cap.name, "risk": cap.risk, "reason": risk_reason(cap.risk)}
            for cap in capabilities
            if cap.risk in {"write", "destructive", "external_side_effect"}
        ],
        "assumptions": assumptions,
        "tool_enhancements": {
            cap.name: {
                "description": cap.description,
                "when_to_use": f"Use for {cap.action} operations on {cap.resource} in the {cap.domain} domain.",
                "caveats": risk_reason(cap.risk),
            }
            for cap in capabilities
        },
        "risk_assessments": {
            cap.name: {
                "risk": cap.risk,
                "reason": risk_reason(cap.risk),
                "reversible": cap.risk not in {"destructive", "external_side_effect"},
            }
            for cap in capabilities
        },
        "additional_tools": [],
        "local_basic_metrics": {
            "domain_count": len(domains),
            "capability_count": len(capabilities),
            "action_counts": dict(action_counts.most_common()),
            "risk_counts": dict(risk_counts.most_common()),
        },
    }


def build_local_system_prompt(kit_name: str, capabilities: list[Capability], input_paths: list[Path]) -> str:
    lines = [
        f"# {kit_name} Assistant",
        "",
        "You operate an existing system through AgentBridge-generated tools.",
        "This kit was produced with local basic project analysis. Treat tool metadata as grounded in discovered project evidence, and ask for clarification when business semantics are ambiguous.",
        "Validate tool arguments before calling tools. Use dry-run output to explain planned changes.",
        "Do not perform destructive or external-side-effect operations unless the user explicitly confirms them.",
    ]
    if input_paths:
        lines.extend(["", "## Project Inputs"])
        for path in input_paths[:8]:
            lines.append(f"- `{path}`")
    lines.extend(["", "## Main Tools"])
    for cap in _rank_local_capabilities(capabilities)[:60]:
        lines.append(f"- `{cap.name}`: {cap.description} Risk: `{cap.risk}`. Source: `{cap.source.kind}` {cap.source.location}.")
    if len(capabilities) > 60:
        lines.append(f"- {len(capabilities) - 60} additional tools are available in `capabilities.json`.")
    return "\n".join(lines) + "\n"


def build_local_skills(capabilities: list[Capability]) -> dict[str, str]:
    skills: dict[str, list[str]] = {}
    by_domain: dict[str, list[Capability]] = defaultdict(list)
    for cap in capabilities:
        by_domain[cap.domain].append(cap)
    for domain, caps in sorted(by_domain.items()):
        ranked = _rank_local_capabilities(caps)
        lines = skills.setdefault(
            domain,
            [
                f"# {domain.title()} Skill",
                "",
                f"Use this skill for {domain} workflows discovered from the target project.",
                "",
                "## Available Tools",
            ],
        )
        for cap in ranked:
            lines.append(
                f"- `{cap.name}`: {cap.action} `{cap.resource}`. Risk: `{cap.risk}`. Evidence: `{cap.source.kind}` {cap.source.location}."
            )
    return {domain: "\n".join(lines) + "\n" for domain, lines in skills.items()}


def _local_project_summary(
    capabilities: list[Capability],
    input_paths: list[Path],
    kit_name: str | None = None,
) -> str:
    domains = Counter(cap.domain for cap in capabilities)
    actions = Counter(cap.action for cap in capabilities)
    risks = Counter(cap.risk for cap in capabilities)
    path_hint = _project_path_hint(input_paths)
    prefix = f"{kit_name} covers" if kit_name else "Project covers"
    return (
        f"{prefix} {len(capabilities)} discovered capabilities across {len(domains)} domain(s)"
        f"{path_hint}. Top domains: {_format_counter(domains, 6)}. "
        f"Top actions: {_format_counter(actions, 6)}. Risk profile: {_format_counter(risks, 4)}."
    )


def _rank_local_capabilities(capabilities: list[Capability]) -> list[Capability]:
    domain_counts = Counter(cap.domain for cap in capabilities)
    action_priority = {
        "create": 8,
        "update": 7,
        "list": 6,
        "get": 5,
        "find": 4,
        "generate": 4,
        "delete": 3,
        "remove": 3,
        "inspect": 1,
    }
    source_priority = {
        "openapi": 5,
        "graphql": 4,
        "source_route": 3,
        "database_schema": 2,
        "warning": 0,
    }
    return sorted(
        capabilities,
        key=lambda cap: (
            -domain_counts[cap.domain],
            -action_priority.get(cap.action, 2),
            -source_priority.get(cap.source.kind, 1),
            cap.domain,
            cap.resource,
            cap.name,
        ),
    )


def _domain_description(domain: str, capabilities: list[Capability]) -> str:
    resources = sorted({cap.resource for cap in capabilities})
    actions = sorted({cap.action for cap in capabilities})
    resource_text = ", ".join(resources[:8])
    action_text = ", ".join(actions[:8])
    return f"{domain} domain covering {action_text} operations for {resource_text}."


def _project_path_hint(input_paths: list[Path]) -> str:
    roots = [path for path in input_paths if path]
    if not roots:
        return ""
    names = []
    for path in roots[:3]:
        try:
            names.append(path.resolve().name)
        except OSError:
            names.append(path.name)
    return f" from {', '.join(name for name in names if name)}"


def _format_counter(counter: Counter[str], limit: int) -> str:
    return ", ".join(f"{name}={count}" for name, count in counter.most_common(limit)) or "none"
