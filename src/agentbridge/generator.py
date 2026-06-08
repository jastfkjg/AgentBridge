from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import Counter, defaultdict
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
        progress_interval: float | None = None,
        analysis_batch_size: int | None = None,
        resume: bool = False,
        analysis_capability_limit: int | None = None,
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
        self.progress_interval = progress_interval if progress_interval is not None else 15.0
        configured_batch_size = analysis_batch_size if analysis_batch_size is not None else analysis_capability_limit
        self.analysis_batch_size = configured_batch_size if configured_batch_size is not None else _env_int("AGENTBRIDGE_AI_BATCH_SIZE", 30)
        self.resume = resume

    def generate(self, input_paths: list[Path], output_dir: Path, name: str | None = None) -> IntegrationKit:
        status_lock = threading.Lock()
        self._progress("Checking output boundary...")
        validate_output_boundary(input_paths, output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        self._write_status(output_dir, "discovering", "Discovering candidate capabilities.", lock=status_lock)
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
            lock=status_lock,
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
                    lock=status_lock,
                )
                analysis_generator = StaticAIGenerator()

        ai_result, analysis_batches = self._run_analysis_batches(
            analysis_generator,
            rule_capabilities,
            kit_name,
            input_paths,
            output_dir,
            status_lock,
        )
        analyzed_capabilities = ai_result["enhanced_capabilities"]
        existing_names = {cap.name for cap in rule_capabilities}
        extra_capabilities = [cap for cap in analyzed_capabilities if cap.name not in existing_names]
        capabilities = list(rule_capabilities) + extra_capabilities
        analysis_capability_count = sum(len(batch) for batch in analysis_batches)
        self._progress(f"Analysis complete. Writing {len(capabilities)} capabilities to {output_dir}...")

        kit = IntegrationKit(name=kit_name, capabilities=capabilities, output_dir=str(output_dir))
        self._write_status(
            output_dir,
            "writing",
            "Analysis complete. Writing kit files.",
            kit_name=kit_name,
            candidate_capability_count=len(rule_capabilities),
            analysis_capability_count=analysis_capability_count,
            analysis_batch_count=len(analysis_batches),
            capability_count=len(capabilities),
            lock=status_lock,
        )

        self._write_json(output_dir / "manifest.json", kit.to_manifest())
        self._write_json(output_dir / "capabilities.json", [cap.to_dict() for cap in capabilities])
        rule_signals = ai_result.get("rule_signals", {
            "candidate_capabilities": [cap.to_dict() for cap in rule_capabilities],
        })
        rule_signals["candidate_capabilities"] = [cap.to_dict() for cap in rule_capabilities]
        rule_signals["batch_enhancement"] = {
            "batch_count": len(analysis_batches),
            "batch_size": self._batch_size_for(rule_capabilities),
            "batches": [
                {
                    "index": index,
                    "capability_count": len(batch),
                    "capabilities": [cap.name for cap in batch],
                }
                for index, batch in enumerate(analysis_batches, start=1)
            ],
        }
        self._write_json(output_dir / "analysis" / "rule_signals.json", rule_signals)
        self._write_json(output_dir / "analysis" / "agent_analysis.json", ai_result.get("agent_analysis", {}))
        self._write_text(output_dir / "spec" / "kit-protocol.md", build_kit_protocol_doc())
        self._write_json(output_dir / "tools" / "mcp_tools.json", build_mcp_tools(capabilities))
        self._write_json(output_dir / "tools" / "openai_tools.json", build_openai_tools(capabilities))
        self._write_json(output_dir / "tools" / "claude_tools.json", build_claude_tools(capabilities))
        self._write_text(output_dir / "tools" / "vercel_ai_tools.ts", build_vercel_tools(capabilities))
        self._write_json(output_dir / "resources" / "schema.json", build_resource_schema(capabilities))
        self._write_json(output_dir / "guardrails" / "permissions.json", build_guardrails(capabilities))
        self._write_json(output_dir / "tests" / "tool_invocation_tests.json", build_invocation_tests(capabilities))
        self._write_text(output_dir / "tests" / "test_generated_tools.py", build_generated_test_file())
        self._write_json(output_dir / "dry_run_plan.json", build_dry_run_plan(capabilities))
        client_config = MCPClientConfig(kit_dir=output_dir, server_name=kit_name)
        self._write_json(output_dir / "clients" / "mcp-client-configs.json", build_mcp_client_configs(client_config))
        self._write_text(output_dir / "clients" / "README.md", build_clients_readme(kit_name, client_config))

        system_prompt = ai_result.get("system_prompt", "")
        if system_prompt:
            self._write_text(output_dir / "prompts" / "system.md", system_prompt)

        skills = ai_result.get("skills", {})
        for domain, content in skills.items():
            if content:
                self._write_text(output_dir / "skills" / f"{domain}.md", content)

        self._write_status(
            output_dir,
            "complete",
            "Generated AgentBridge kit.",
            kit_name=kit_name,
            candidate_capability_count=len(rule_capabilities),
            analysis_capability_count=analysis_capability_count,
            analysis_batch_count=len(analysis_batches),
            capability_count=len(capabilities),
            lock=status_lock,
        )
        self._progress(f"Generated AgentBridge kit at {output_dir}.")
        return kit

    def _progress(self, message: str) -> None:
        if self.progress:
            self.progress(message)

    def _should_offer_ai_confirmation(self, ai_generator: AIGenerator) -> bool:
        return bool(self.confirm_ai_analysis and getattr(ai_generator, "model", "") != "static")

    def _run_analysis_batches(
        self,
        analysis_generator: AIGenerator,
        rule_capabilities: list[Capability],
        kit_name: str,
        input_paths: list[Path],
        output_dir: Path,
        status_lock: threading.Lock,
    ) -> tuple[dict[str, Any], list[list[Capability]]]:
        model = getattr(analysis_generator, "model", "")
        batches = [rule_capabilities] if model == "static" else self._build_analysis_batches(rule_capabilities)
        batch_count = len(batches)
        if model == "static":
            self._progress("Generating deterministic kit metadata...")
        else:
            model_note = f" using {model}" if model else ""
            self._progress(
                f"Running AI project analysis{model_note} in {batch_count} batch(es); "
                "completed batches can be resumed."
            )

        completed_indices: set[int] = set()
        batch_results: list[dict[str, Any]] = []
        self._write_resume_state(output_dir, kit_name, input_paths, rule_capabilities, batches, completed_indices, "running")

        for batch_index, batch in enumerate(batches, start=1):
            batch_path = self._batch_result_path(output_dir, batch_index)
            batch_result = self._load_batch_result(batch_path, batch) if self.resume else None
            if batch_result is not None:
                self._progress(f"Resuming from completed AI batch {batch_index}/{batch_count}: {batch_path}")
                self._write_status(
                    output_dir,
                    "analyzing",
                    f"Resumed completed AI batch {batch_index}/{batch_count}.",
                    kit_name=kit_name,
                    candidate_capability_count=len(rule_capabilities),
                    analysis_batch_size=self._batch_size_for(rule_capabilities),
                    analysis_batch_count=batch_count,
                    current_batch_index=batch_index,
                    current_batch_capability_count=len(batch),
                    current_batch_capabilities=[cap.name for cap in batch],
                    completed_batch_count=len(completed_indices),
                    lock=status_lock,
                )
            else:
                preview = ", ".join(cap.name for cap in batch[:5])
                if len(batch) > 5:
                    preview += ", ..."
                self._progress(f"Enhancing AI batch {batch_index}/{batch_count} ({len(batch)} capabilities): {preview}")
                self._write_status(
                    output_dir,
                    "analyzing",
                    f"Enhancing AI batch {batch_index}/{batch_count}.",
                    kit_name=kit_name,
                    candidate_capability_count=len(rule_capabilities),
                    analysis_batch_size=self._batch_size_for(rule_capabilities),
                    analysis_batch_count=batch_count,
                    current_batch_index=batch_index,
                    current_batch_capability_count=len(batch),
                    current_batch_capabilities=[cap.name for cap in batch],
                    completed_batch_count=len(completed_indices),
                    lock=status_lock,
                )
                batch_result = self._run_single_analysis_batch(
                    analysis_generator,
                    batch,
                    kit_name,
                    input_paths,
                    output_dir,
                    status_lock,
                    batch_index,
                    batch_count,
                    len(rule_capabilities),
                )
                self._write_json(batch_path, batch_result)

            batch_results.append(batch_result)
            completed_indices.add(batch_index)
            self._write_resume_state(
                output_dir,
                kit_name,
                input_paths,
                rule_capabilities,
                batches,
                completed_indices,
                "running" if len(completed_indices) < batch_count else "complete",
            )

        return self._merge_batch_results(rule_capabilities, batch_results), batches

    def _run_single_analysis_batch(
        self,
        analysis_generator: AIGenerator,
        batch: list[Capability],
        kit_name: str,
        input_paths: list[Path],
        output_dir: Path,
        status_lock: threading.Lock,
        batch_index: int,
        batch_count: int,
        total_capability_count: int,
    ) -> dict[str, Any]:
        model = getattr(analysis_generator, "model", "")
        heartbeat_stop = threading.Event()
        heartbeat_thread = None
        if model != "static":
            heartbeat_thread = threading.Thread(
                target=self._analysis_heartbeat,
                args=(
                    heartbeat_stop,
                    output_dir,
                    kit_name,
                    total_capability_count,
                    batch_index,
                    batch_count,
                    len(batch),
                    status_lock,
                ),
                daemon=True,
            )
            heartbeat_thread.start()
        batch_input_paths = self._batch_source_paths(batch, input_paths)
        if model != "static":
            self._progress(
                f"AI batch {batch_index}/{batch_count} source context: "
                f"{len(batch_input_paths)} file(s), not the full project tree."
            )
        try:
            if hasattr(analysis_generator, "set_progress"):
                analysis_generator.set_progress(self.progress)
            ai_result = analysis_generator.generate_all(batch, kit_name, input_paths=batch_input_paths)
        except Exception as exc:
            self._write_status(
                output_dir,
                "failed",
                str(exc),
                kit_name=kit_name,
                candidate_capability_count=total_capability_count,
                analysis_batch_count=batch_count,
                current_batch_index=batch_index,
                current_batch_capability_count=len(batch),
                lock=status_lock,
            )
            raise
        finally:
            heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=1.0)

        return {
            "batch_index": batch_index,
            "batch_count": batch_count,
            "capability_names": [cap.name for cap in batch],
            "enhanced_capabilities": [
                cap.to_dict() if isinstance(cap, Capability) else cap
                for cap in ai_result.get("enhanced_capabilities", [])
            ],
            "agent_analysis": ai_result.get("agent_analysis", {}),
            "rule_signals": ai_result.get("rule_signals", {}),
            "system_prompt": ai_result.get("system_prompt", ""),
            "skills": ai_result.get("skills", {}),
        }

    def _merge_batch_results(
        self,
        rule_capabilities: list[Capability],
        batch_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        capabilities_by_name = {cap.name: cap for cap in rule_capabilities}
        extras: list[Capability] = []
        extra_names = set(capabilities_by_name)
        analyses: list[dict[str, Any]] = []
        system_prompt = ""
        skills: dict[str, str] = {}

        for result in batch_results:
            for raw_cap in result.get("enhanced_capabilities", []):
                cap = raw_cap if isinstance(raw_cap, Capability) else Capability.from_dict(raw_cap)
                if cap.name in capabilities_by_name:
                    capabilities_by_name[cap.name] = cap
                elif cap.name not in extra_names:
                    extras.append(cap)
                    extra_names.add(cap.name)
            analysis = result.get("agent_analysis", {})
            if isinstance(analysis, dict):
                analyses.append(analysis)
            if not system_prompt and result.get("system_prompt"):
                system_prompt = str(result["system_prompt"])
            for domain, content in (result.get("skills", {}) or {}).items():
                if content and domain not in skills:
                    skills[str(domain)] = str(content)

        capabilities = [capabilities_by_name[cap.name] for cap in rule_capabilities] + extras
        return {
            "enhanced_capabilities": capabilities,
            "rule_signals": {
                "candidate_capabilities": [cap.to_dict() for cap in rule_capabilities],
                "batch_results": [
                    {
                        "batch_index": result.get("batch_index"),
                        "capability_count": len(result.get("capability_names", [])),
                        "capabilities": result.get("capability_names", []),
                    }
                    for result in batch_results
                ],
            },
            "agent_analysis": self._merge_agent_analyses(analyses),
            "system_prompt": system_prompt,
            "skills": skills,
        }

    def _merge_agent_analyses(self, analyses: list[dict[str, Any]]) -> dict[str, Any]:
        merged: dict[str, Any] = {
            "summary": "",
            "business_objects": [],
            "workflows": [],
            "permission_boundaries": [],
            "side_effects": [],
            "assumptions": [],
            "tool_enhancements": {},
            "risk_assessments": {},
            "additional_tools": [],
        }
        seen: dict[str, set[str]] = {
            "business_objects": set(),
            "workflows": set(),
            "permission_boundaries": set(),
            "side_effects": set(),
            "assumptions": set(),
            "additional_tools": set(),
        }
        for analysis in analyses:
            if not merged["summary"] and analysis.get("summary"):
                merged["summary"] = analysis["summary"]
            for key in ("business_objects", "workflows", "permission_boundaries", "side_effects", "assumptions", "additional_tools"):
                for item in analysis.get(key, []) or []:
                    marker = json.dumps(item, sort_keys=True, default=str)
                    if marker not in seen[key]:
                        merged[key].append(item)
                        seen[key].add(marker)
            for key in ("tool_enhancements", "risk_assessments"):
                value = analysis.get(key, {})
                if isinstance(value, dict):
                    merged[key].update(value)
        return merged

    def _build_analysis_batches(self, capabilities: list[Capability]) -> list[list[Capability]]:
        ranked = self._rank_analysis_capabilities(capabilities)
        batch_size = self._batch_size_for(capabilities)
        if batch_size <= 0 or batch_size >= len(ranked):
            return [ranked]
        return [ranked[index : index + batch_size] for index in range(0, len(ranked), batch_size)]

    def _rank_analysis_capabilities(self, capabilities: list[Capability]) -> list[Capability]:
        if len(capabilities) <= 1:
            return capabilities

        domain_counts = Counter(cap.domain for cap in capabilities)
        action_counts = Counter(cap.action for cap in capabilities)
        source_kind_priority = {
            "openapi": 5,
            "graphql": 4,
            "source_route": 3,
            "database_schema": 2,
            "warning": 0,
            "ai_inferred": 1,
        }
        risk_priority = {
            "external_side_effect": 4,
            "destructive": 3,
            "write": 2,
            "read": 1,
        }
        ranked: list[tuple[int, int, Capability]] = []
        for index, cap in enumerate(capabilities):
            score = (
                domain_counts[cap.domain] * 1000
                + action_counts[cap.action] * 100
                + source_kind_priority.get(cap.source.kind, 1) * 10
                + risk_priority.get(cap.risk, 1)
            )
            ranked.append((score, index, cap))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [cap for _score, _index, cap in ranked]

    def _batch_size_for(self, capabilities: list[Capability]) -> int:
        batch_size = int(self.analysis_batch_size or 0)
        if batch_size <= 0:
            return len(capabilities)
        return batch_size

    def _batch_source_paths(self, batch: list[Capability], input_paths: list[Path]) -> list[Path]:
        roots = [path.resolve() for path in input_paths if path.exists()]
        seen: set[Path] = set()
        result: list[Path] = []

        for cap in batch:
            path = Path(cap.source.path)
            candidates = [path]
            if not path.is_absolute():
                candidates.append(Path.cwd() / path)
                for root in roots:
                    if root.is_dir():
                        candidates.append(root / path)
                        candidates.append(root / path.name)
            for candidate in candidates:
                try:
                    resolved = candidate.resolve()
                except OSError:
                    continue
                if resolved.is_file() and resolved not in seen:
                    result.append(resolved)
                    seen.add(resolved)
                    break

        if not result:
            for path in input_paths:
                if path.is_file():
                    resolved = path.resolve()
                    if resolved not in seen:
                        result.append(resolved)
                        seen.add(resolved)

        return result

    def _batch_result_path(self, output_dir: Path, batch_index: int) -> Path:
        return output_dir / "analysis" / "batches" / f"batch_{batch_index:04d}.json"

    def _load_batch_result(self, path: Path, batch: list[Capability]) -> dict[str, Any] | None:
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        expected_names = [cap.name for cap in batch]
        if result.get("capability_names") != expected_names:
            self._progress(f"Existing batch result does not match current plan; regenerating: {path}")
            return None
        return result

    def _write_resume_state(
        self,
        output_dir: Path,
        kit_name: str,
        input_paths: list[Path],
        capabilities: list[Capability],
        batches: list[list[Capability]],
        completed_indices: set[int],
        status: str,
    ) -> None:
        batch_infos = []
        for index, batch in enumerate(batches, start=1):
            result_path = self._batch_result_path(output_dir, index).relative_to(output_dir)
            batch_infos.append(
                {
                    "index": index,
                    "status": "complete" if index in completed_indices else "pending",
                    "capability_count": len(batch),
                    "capabilities": [cap.name for cap in batch],
                    "result_path": str(result_path),
                }
            )
        write_json(
            output_dir / "analysis" / "resume_state.json",
            {
                "version": 1,
                "status": status,
                "kit_name": kit_name,
                "input_paths": [str(path.resolve()) for path in input_paths],
                "candidate_capability_count": len(capabilities),
                "analysis_batch_size": self._batch_size_for(capabilities),
                "analysis_batch_count": len(batches),
                "completed_batch_count": len(completed_indices),
                "remaining_batch_count": len(batches) - len(completed_indices),
                "completed_batches": sorted(completed_indices),
                "batches": batch_infos,
            },
        )

    def _write_status(self, output_dir: Path, status: str, message: str, **extra: Any) -> None:
        lock = extra.pop("lock", None)
        if lock is not None:
            with lock:
                write_json(output_dir / "generation_status.json", {
                    "status": status,
                    "message": message,
                    **extra,
                })
            return
        write_json(output_dir / "generation_status.json", {
            "status": status,
            "message": message,
            **extra,
        })

    def _write_json(self, path: Path, data: Any) -> None:
        self._progress(f"Writing kit file: {path}")
        write_json(path, data)

    def _write_text(self, path: Path, data: str) -> None:
        self._progress(f"Writing kit file: {path}")
        write_text(path, data)

    def _analysis_heartbeat(
        self,
        stop_event: threading.Event,
        output_dir: Path,
        kit_name: str,
        total_capability_count: int,
        batch_index: int,
        batch_count: int,
        batch_capability_count: int,
        status_lock: threading.Lock,
    ) -> None:
        started = time.monotonic()
        interval = float(self.progress_interval or 0.0)
        if interval <= 0:
            return
        while not stop_event.wait(interval):
            elapsed = int(time.monotonic() - started)
            message = (
                f"Still waiting for AI batch {batch_index}/{batch_count} "
                f"({elapsed}s elapsed, {batch_capability_count} capabilities in this batch, "
                f"{total_capability_count} total)."
            )
            self._progress(message)
            self._write_status(
                output_dir,
                "analyzing",
                message,
                kit_name=kit_name,
                candidate_capability_count=total_capability_count,
                analysis_batch_count=batch_count,
                current_batch_index=batch_index,
                current_batch_capability_count=batch_capability_count,
                elapsed_seconds=elapsed,
                lock=status_lock,
            )


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "")
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed >= 0 else default


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
