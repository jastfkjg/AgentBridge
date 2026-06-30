from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

RiskLevel = Literal["read", "write", "destructive", "external_side_effect"]
KIT_PROTOCOL_VERSION = "agentbridge-kit/v1"


@dataclass(frozen=True)
class SourceRef:
    kind: str
    path: str
    location: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "path": self.path, "location": self.location}


@dataclass
class Capability:
    name: str
    domain: str
    resource: str
    action: str
    description: str
    input_schema: dict[str, Any]
    risk: RiskLevel
    confirm_required: bool
    source: SourceRef
    transport: dict[str, Any] = field(default_factory=dict)
    dry_run_supported: bool = True
    evidence: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.6

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "domain": self.domain,
            "resource": self.resource,
            "action": self.action,
            "description": self.description,
            "input_schema": self.input_schema,
            "risk": self.risk,
            "confirm_required": self.confirm_required,
            "source": self.source.to_dict(),
            "transport": self.transport,
            "dry_run_supported": self.dry_run_supported,
            "evidence": self.evidence,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Capability":
        source = data.get("source", {})
        return cls(
            name=data["name"],
            domain=data["domain"],
            resource=data["resource"],
            action=data["action"],
            description=data.get("description", data["name"]),
            input_schema=data.get("input_schema", {"type": "object", "properties": {}}),
            risk=data.get("risk", "read"),
            confirm_required=bool(data.get("confirm_required", False)),
            source=SourceRef(
                kind=source.get("kind", "unknown"),
                path=source.get("path", ""),
                location=source.get("location", ""),
            ),
            transport=data.get("transport", {}),
            dry_run_supported=bool(data.get("dry_run_supported", True)),
            evidence=[item for item in data.get("evidence", []) if isinstance(item, dict)],
            confidence=float(data.get("confidence", 0.6) or 0.0),
        )


@dataclass
class IntegrationKit:
    name: str
    capabilities: list[Capability]
    output_dir: str

    def to_manifest(self) -> dict[str, Any]:
        domains = sorted({cap.domain for cap in self.capabilities})
        risks = {risk: 0 for risk in ["read", "write", "destructive", "external_side_effect"]}
        for cap in self.capabilities:
            risks[cap.risk] = risks.get(cap.risk, 0) + 1
        return {
            "protocol": KIT_PROTOCOL_VERSION,
            "name": self.name,
            "version": "0.1.0",
            "capability_count": len(self.capabilities),
            "domains": domains,
            "risk_summary": risks,
            "outputs": {
                "kit_root": ".",
                "capabilities": "capabilities.json",
                "rule_signals": "analysis/rule_signals.json",
                "ai_analysis": "analysis/agent_analysis.json",
                "analysis_report": "analysis/report.md",
                "kit_protocol": "spec/kit-protocol.md",
                "mcp_tools": "tools/mcp_tools.json",
                "openai_tools": "tools/openai_tools.json",
                "claude_tools": "tools/claude_tools.json",
                "vercel_ai_tools": "tools/vercel_ai_tools.ts",
                "skills": "skills/",
                "prompts": "prompts/system.md",
                "resource_schema": "resources/schema.json",
                "guardrails": "guardrails/permissions.json",
                "tests": "tests/",
                "dry_run_plan": "dry_run_plan.json",
                "client_configs": "clients/mcp-client-configs.json",
                "client_setup": "clients/README.md",
            },
        }
