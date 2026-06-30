from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentbridge.models import KIT_PROTOCOL_VERSION
from agentbridge.policy import default_policy, normalize_permissions_policy, risk_is_denied, risk_requires_confirmation

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
    re.compile(r"(?i)authorization\s*[:=]\s*['\"]Bearer\s+[^'\"]+['\"]"),
]


@dataclass
class CheckResult:
    name: str
    ok: bool
    message: str
    severity: str = "error"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "message": self.message, "severity": self.severity}


@dataclass
class KitReport:
    kit_dir: Path
    checks: list[CheckResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(check.ok or check.severity == "warning" for check in self.checks)

    def add(self, name: str, ok: bool, message: str, severity: str = "error") -> None:
        self.checks.append(CheckResult(name=name, ok=ok, message=message, severity=severity))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kit_dir": str(self.kit_dir),
            "ok": self.ok,
            "summary": self.summary,
            "checks": [check.to_dict() for check in self.checks],
        }


def validate_kit(kit_dir: Path) -> KitReport:
    report = KitReport(kit_dir=kit_dir)
    manifest = load_json_file(kit_dir / "manifest.json", report, "manifest")
    capabilities = load_json_file(kit_dir / "capabilities.json", report, "capabilities")
    guardrails = load_json_file(kit_dir / "guardrails" / "permissions.json", report, "guardrails")

    if isinstance(manifest, dict):
        protocol = manifest.get("protocol")
        report.add("protocol", protocol == KIT_PROTOCOL_VERSION, f"protocol={protocol!r}, expected {KIT_PROTOCOL_VERSION!r}")
        outputs = manifest.get("outputs", {})
        if isinstance(outputs, dict):
            for key, rel in sorted(outputs.items()):
                if key in {"kit_root", "skills", "tests"}:
                    continue
                path = kit_dir / str(rel)
                report.add(f"output:{key}", path.exists(), f"{rel} {'exists' if path.exists() else 'is missing'}")

    caps_by_name: dict[str, dict[str, Any]] = {}
    if isinstance(capabilities, list):
        for index, cap in enumerate(capabilities):
            if not isinstance(cap, dict):
                report.add(f"capability:{index}", False, "Capability entry is not an object")
                continue
            name = cap.get("name")
            report.add(f"capability:{name or index}:name", isinstance(name, str) and bool(name), "Capability has a name")
            if isinstance(name, str):
                if name in caps_by_name:
                    report.add(f"capability:{name}:unique", False, "Duplicate capability name")
                caps_by_name[name] = cap
            schema = cap.get("input_schema", {})
            report.add(f"capability:{name or index}:schema", isinstance(schema, dict) and schema.get("type") == "object", "Input schema is an object")
    elif capabilities is not None:
        report.add("capabilities:type", False, "capabilities.json must contain an array")

    guardrail_tools: dict[str, Any] = {}
    if isinstance(guardrails, dict):
        guardrail_tools = guardrails.get("tools", {})
        report.add("guardrails:tools", isinstance(guardrail_tools, dict), "Guardrails contain a tools object")
        policy = normalize_permissions_policy(guardrails)
        report.add("guardrails:policy", isinstance(policy.get("risk_actions"), dict), "Guardrails contain a normalized permission policy")
    if isinstance(guardrail_tools, dict):
        for name, cap in caps_by_name.items():
            rule = guardrail_tools.get(name)
            report.add(f"guardrail:{name}", isinstance(rule, dict), "Capability has a guardrail")
            if not isinstance(rule, dict):
                continue
            risk = rule.get("risk", cap.get("risk"))
            if risk in {"write", "destructive", "external_side_effect"}:
                protected = risk_is_denied(guardrails if isinstance(guardrails, dict) else {}, str(risk)) or risk_requires_confirmation(
                    guardrails if isinstance(guardrails, dict) else {},
                    str(risk),
                    bool(rule.get("confirm_required")),
                )
                report.add(f"guardrail:{name}:policy", protected, "Mutating or side-effect tool is denied or requires confirmation")
            transport = rule.get("transport", cap.get("transport", {}))
            if isinstance(transport, dict) and transport.get("type") == "http":
                report.add(f"transport:{name}:method", bool(transport.get("method")), "HTTP transport has method")
                report.add(f"transport:{name}:path", bool(transport.get("path")), "HTTP transport has path")

    secret_hits = scan_for_secrets(kit_dir)
    report.add("secrets", not secret_hits, "No obvious secrets found in generated kit" if not secret_hits else f"Potential secrets: {', '.join(secret_hits[:5])}", severity="warning")

    risks: dict[str, int] = {}
    for cap in caps_by_name.values():
        risk = str(cap.get("risk", "unknown"))
        risks[risk] = risks.get(risk, 0) + 1
    report.summary = {
        "capability_count": len(caps_by_name),
        "risk_summary": risks,
        "high_risk_tools": [
            name for name, cap in caps_by_name.items() if cap.get("risk") in {"destructive", "external_side_effect"}
        ],
    }
    return report


def doctor_kit(kit_dir: Path, base_url: str = "", execute: bool = False) -> KitReport:
    report = validate_kit(kit_dir)
    report.add("mode", True, "Execution mode enabled" if execute else "Dry-run mode enabled", severity="info")
    if execute:
        report.add("base_url", bool(base_url), "Execution mode has a target base URL")
    else:
        report.add("base_url", True, "Base URL not required for dry-run mode", severity="info")
    high_risk = report.summary.get("high_risk_tools", [])
    if high_risk:
        report.add("high_risk", True, f"High-risk tools require confirmation: {', '.join(high_risk)}", severity="info")
    return report


def migrate_kit(kit_dir: Path) -> dict[str, Any]:
    changed: list[str] = []
    manifest_path = kit_dir / "manifest.json"
    manifest = _load_json_no_report(manifest_path)
    if not isinstance(manifest, dict):
        manifest = {"protocol": KIT_PROTOCOL_VERSION, "name": kit_dir.name}
    manifest.setdefault("protocol", KIT_PROTOCOL_VERSION)
    outputs = manifest.setdefault("outputs", {})
    if isinstance(outputs, dict) and outputs.get("analysis_report") != "analysis/report.md":
        outputs["analysis_report"] = "analysis/report.md"
        changed.append("manifest.outputs.analysis_report")
    if manifest.get("protocol") != KIT_PROTOCOL_VERSION:
        manifest["protocol"] = KIT_PROTOCOL_VERSION
        changed.append("manifest.protocol")
    _write_json_if_changed(manifest_path, manifest, changed, "manifest.json")

    guardrails_path = kit_dir / "guardrails" / "permissions.json"
    guardrails = _load_json_no_report(guardrails_path)
    if not isinstance(guardrails, dict):
        guardrails = {"tools": {}}
    if "policy" not in guardrails:
        guardrails["policy"] = default_policy()
        changed.append("guardrails.policy")
    else:
        normalized = normalize_permissions_policy(guardrails)
        if guardrails.get("policy") != normalized:
            guardrails["policy"] = normalized
            changed.append("guardrails.policy")
    guardrails.setdefault("tools", {})
    _write_json_if_changed(guardrails_path, guardrails, changed, "guardrails/permissions.json")

    report_path = kit_dir / "analysis" / "report.md"
    if not report_path.exists():
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("# AgentBridge Analysis Report\n\nMigrated legacy kit. Regenerate to refresh detailed analysis.\n", encoding="utf-8")
        changed.append("analysis/report.md")

    return {"changed": bool(changed), "changes": changed}


def load_json_file(path: Path, report: KitReport, name: str) -> Any:
    if not path.exists():
        report.add(name, False, f"{path.relative_to(report.kit_dir)} is missing")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.add(name, False, f"Could not read {path}: {exc}")
        return None
    report.add(name, True, f"{path.relative_to(report.kit_dir)} is readable")
    return data


def _load_json_no_report(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json_if_changed(path: Path, data: Any, changed: list[str], label: str) -> None:
    serialized = json.dumps(data, indent=2, sort_keys=True) + "\n"
    try:
        current = path.read_text(encoding="utf-8")
    except OSError:
        current = ""
    if current == serialized:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialized, encoding="utf-8")
    if label not in changed:
        changed.append(label)


def scan_for_secrets(kit_dir: Path) -> list[str]:
    hits: list[str] = []
    for path in kit_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".json", ".md", ".ts", ".txt", ".py"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            hits.append(str(path.relative_to(kit_dir)))
    return hits


def format_report(report: KitReport) -> str:
    lines = [f"Kit: {report.kit_dir}", f"Status: {'OK' if report.ok else 'FAILED'}"]
    if report.summary:
        lines.append(f"Capabilities: {report.summary.get('capability_count', 0)}")
        lines.append(f"Risks: {json.dumps(report.summary.get('risk_summary', {}), sort_keys=True)}")
    for check in report.checks:
        marker = "OK" if check.ok else "WARN" if check.severity == "warning" else "FAIL"
        lines.append(f"[{marker}] {check.name}: {check.message}")
    return "\n".join(lines)
