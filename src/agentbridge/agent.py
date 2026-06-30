from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import os
import queue
import re
import shlex
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from agentbridge.models import Capability
from agentbridge.policy import classify_risk, confirmation_required, risk_reason

_DEFAULT_MODEL = "claude-sonnet-4-20250514"
_MAX_SOURCE_BYTES = 200_000


class AIGenerator:
    _BACKEND_AGENT_SDK = "agent-sdk"
    _BACKEND_ANTHROPIC = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        progress: Callable[[str], None] | None = None,
        analysis_mode: str | None = None,
        agent_plan_timeout: float | None = None,
        agent_batch_timeout: float | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "LLM API key is required. "
                "Set ANTHROPIC_API_KEY environment variable or pass api_key parameter."
            )
        self.base_url = base_url or os.environ.get("ANTHROPIC_BASE_URL", "")
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "") or _DEFAULT_MODEL
        self.timeout = timeout if timeout is not None else _env_float("AGENTBRIDGE_LLM_TIMEOUT", 300.0)
        self.agent_plan_timeout = (
            agent_plan_timeout
            if agent_plan_timeout is not None
            else _env_float("AGENTBRIDGE_AGENT_PLAN_TIMEOUT", 120.0)
        )
        self.agent_batch_timeout = (
            agent_batch_timeout
            if agent_batch_timeout is not None
            else _env_float("AGENTBRIDGE_AGENT_BATCH_TIMEOUT", 180.0)
        )
        self.progress = progress
        self.agentic_guidance = ""
        self.analysis_mode = analysis_mode or os.environ.get("AGENTBRIDGE_ANALYSIS_MODE", "auto")
        if self.analysis_mode not in {"auto", "agentic", "prompt"}:
            raise ValueError("analysis_mode must be one of: auto, agentic, prompt")

        os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")
        os.environ.setdefault("ANTHROPIC_API_KEY", self.api_key)
        if self.base_url:
            os.environ["ANTHROPIC_BASE_URL"] = self.base_url

        self._backend = self._detect_backend()

    def set_progress(self, progress: Callable[[str], None] | None) -> None:
        self.progress = progress

    def set_agentic_guidance(self, guidance: str) -> None:
        self.agentic_guidance = guidance

    def _progress(self, message: str) -> None:
        if self.progress:
            self.progress(message)

    def _detect_backend(self) -> str:
        if self.analysis_mode == "agentic":
            if _claude_agent_sdk_available():
                return self._BACKEND_AGENT_SDK
            raise ImportError(
                "Claude Agent SDK analysis requires 'claude-agent-sdk'. "
                "Install with: pip install agbr[agent]"
            )
        if self.analysis_mode == "prompt":
            if _anthropic_available():
                return self._BACKEND_ANTHROPIC
            raise ImportError(
                "Prompt analysis requires the 'anthropic' package. "
                "Install with: pip install agbr[ai]"
            )
        if _claude_agent_sdk_available():
            return self._BACKEND_AGENT_SDK
        if self.base_url:
            return self._BACKEND_ANTHROPIC
        if _anthropic_available():
            return self._BACKEND_ANTHROPIC
        raise ImportError(
            "AI generation requires either 'claude-agent-sdk' or 'anthropic' package. "
            "Install with: pip install agbr[agent] (recommended) "
            "or pip install agbr[ai]"
        )

    def generate_all(
        self,
        capabilities: list[Capability],
        kit_name: str,
        input_paths: list[Path] | None = None,
    ) -> dict[str, Any]:
        self._progress("Preparing rule context for AI analysis...")
        rule_context = self._build_rule_context(capabilities)
        if self._should_use_agentic_analysis(input_paths or []):
            self._progress(
                "Using Claude Agent SDK agentic analysis: project files will be inspected "
                "through read-only SDK tools instead of copied into one large prompt."
            )
            source_context: dict[str, str] = {}
        else:
            self._progress("Collecting source files for AI context...")
            source_context = self._build_source_context(input_paths or [])
            source_bytes = sum(len(content) for content in source_context.values())
            self._progress(
                f"Prepared AI context with {len(source_context)} source files "
                f"and {source_bytes} characters."
            )
        return _run_async(
            self._generate_all_async(capabilities, kit_name, rule_context, source_context, input_paths)
        )

    def uses_agentic_analysis(self, input_paths: list[Path] | None = None) -> bool:
        return self._should_use_agentic_analysis(input_paths or [])

    def _should_use_agentic_analysis(self, input_paths: list[Path]) -> bool:
        if self._backend != self._BACKEND_AGENT_SDK:
            return False
        if self.analysis_mode == "prompt":
            return False
        if self.analysis_mode == "agentic":
            return True
        return any(path.is_dir() for path in input_paths)

    def _build_rule_context(self, capabilities: list[Capability]) -> dict[str, Any]:
        rule_risks: dict[str, dict[str, Any]] = {}
        for cap in capabilities:
            rule_risks[cap.name] = {
                "rule_based_risk": cap.risk,
                "rule_based_confirm_required": cap.confirm_required,
                "risk_reason": risk_reason(cap.risk),
                "action": cap.action,
                "transport": cap.transport,
            }
        return {
            "rule_based_risk_assessment": rule_risks,
            "risk_policy": {
                "read": {"confirm_required": False},
                "write": {"confirm_required": False},
                "destructive": {"confirm_required": True},
                "external_side_effect": {"confirm_required": True},
            },
        }

    def _build_source_context(self, input_paths: list[Path]) -> dict[str, str]:
        source_files: dict[str, str] = {}
        total_bytes = 0
        readable_exts = {
            ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs",
            ".json", ".yaml", ".yml", ".graphql", ".gql", ".sql",
            ".md", ".txt", ".toml", ".cfg", ".ini", ".env",
            ".html", ".css", ".scss",
        }
        for input_path in input_paths:
            if input_path.is_file():
                if input_path.suffix.lower() in readable_exts:
                    try:
                        content = input_path.read_text(encoding="utf-8", errors="replace")
                        total_bytes += len(content)
                        if total_bytes > _MAX_SOURCE_BYTES:
                            break
                        source_files[str(input_path)] = content
                        self._progress(f"Added source file to AI context: {input_path}")
                    except OSError:
                        pass
            elif input_path.is_dir():
                for root, _dirs, files in os.walk(input_path):
                    root_path = Path(root)
                    if any(part.startswith(".") for part in root_path.parts):
                        continue
                    if any(part in ("node_modules", "__pycache__", ".git", "venv", ".venv", "dist", "build") for part in root_path.parts):
                        continue
                    for fname in sorted(files):
                        fpath = root_path / fname
                        if fpath.suffix.lower() in readable_exts:
                            try:
                                content = fpath.read_text(encoding="utf-8", errors="replace")
                                total_bytes += len(content)
                                if total_bytes > _MAX_SOURCE_BYTES:
                                    break
                                rel = fpath.relative_to(input_path)
                                source_files[str(rel)] = content
                                self._progress(f"Added source file to AI context: {rel}")
                            except OSError:
                                pass
                    if total_bytes > _MAX_SOURCE_BYTES:
                        break
        return source_files

    async def _generate_all_async(
        self,
        capabilities: list[Capability],
        kit_name: str,
        rule_context: dict[str, Any],
        source_context: dict[str, str],
        input_paths: list[Path] | None,
    ) -> dict[str, Any]:
        if self._should_use_agentic_analysis(input_paths or []):
            return await self._generate_all_agentic_async(
                capabilities,
                kit_name,
                rule_context,
                input_paths or [],
            )

        caps_data = [cap.to_dict() for cap in capabilities]
        domains = sorted({cap.domain for cap in capabilities})

        source_section = ""
        if source_context:
            source_section = "\n\nSource code files from the project:\n"
            for path, content in source_context.items():
                source_section += f"\n--- {path} ---\n{content}\n"

        cwd_hint = ""
        if input_paths and self._backend == self._BACKEND_AGENT_SDK:
            first = input_paths[0]
            if first.is_dir():
                cwd_hint = (
                    f"\n\nThe project directory is: {first}\n"
                    "Treat it as read-only. Do not write, edit, move, delete, format, or otherwise modify any project file."
                )

        self._progress(
            f"Sending AI analysis request to backend={self._backend}, model={self.model}."
        )
        self._progress(
            "AI generation is prompt-only for this step: source files are sent as context, "
            "and no local AI tool calls are exposed to trace."
        )
        result = await self._ask(
            PROMPT_GENERATE_ALL_SYSTEM,
            PROMPT_GENERATE_ALL_USER.format(
                capabilities=json.dumps(caps_data, indent=2),
                kit_name=kit_name,
                domains=", ".join(domains),
                rule_context=json.dumps(rule_context, indent=2),
                source_section=source_section,
                cwd_hint=cwd_hint,
            ),
        )

        return self._result_from_generation_text(capabilities, rule_context, result)

    async def _generate_all_agentic_async(
        self,
        capabilities: list[Capability],
        kit_name: str,
        rule_context: dict[str, Any],
        input_paths: list[Path],
    ) -> dict[str, Any]:
        caps_data = [cap.to_dict() for cap in capabilities]
        domains = sorted({cap.domain for cap in capabilities})
        cwd = _project_cwd(input_paths)
        project_paths = "\n".join(f"- {path.resolve()}" for path in input_paths) or "- <none>"
        source_hints = _format_batch_source_hints(capabilities, input_paths=input_paths)
        guidance_section = f"\n\nProject understanding guidance from the previous SDK planning step:\n{self.agentic_guidance}\n" if self.agentic_guidance else ""
        self._progress(
            f"Starting Claude Agent SDK project analysis in {cwd or Path.cwd()} "
            f"for {len(capabilities)} candidate capabilities."
        )
        result = await self._ask_agent_sdk(
            PROMPT_GENERATE_ALL_SYSTEM,
            PROMPT_GENERATE_ALL_AGENTIC_USER.format(
                capabilities=json.dumps(caps_data, indent=2),
                kit_name=kit_name,
                domains=", ".join(domains),
                rule_context=json.dumps(rule_context, indent=2),
                project_paths=project_paths,
                source_hints=source_hints,
                guidance_section=guidance_section,
            ),
            cwd=str(cwd) if cwd else None,
            timeout=_bounded_agent_timeout(self.agent_batch_timeout, self.timeout),
            tools=["Read", "Grep"],
            max_turns=_env_int("AGENTBRIDGE_AGENT_BATCH_MAX_TURNS", 18),
        )

        return self._result_from_generation_text(capabilities, rule_context, result)

    def plan_agentic_analysis(
        self,
        capabilities: list[Capability],
        kit_name: str,
        input_paths: list[Path],
    ) -> dict[str, Any]:
        if not self._should_use_agentic_analysis(input_paths):
            return {}
        rule_context = self._build_rule_context(capabilities)
        return _run_async(self._plan_agentic_analysis_async(capabilities, kit_name, input_paths, rule_context))

    async def _plan_agentic_analysis_async(
        self,
        capabilities: list[Capability],
        kit_name: str,
        input_paths: list[Path],
        rule_context: dict[str, Any],
    ) -> dict[str, Any]:
        cwd = _project_cwd(input_paths)
        project_paths = "\n".join(f"- {path.resolve()}" for path in input_paths) or "- <none>"
        inventory = _build_agentic_plan_inventory(capabilities)
        plan_source_hints = _format_plan_source_hints(input_paths, inventory)
        plan_source_context = self._build_agentic_plan_source_context(plan_source_hints, input_paths)
        self._progress(
            f"Asking Claude Agent SDK to form a project understanding plan from project files "
            f"and a compact summary of {len(capabilities)} candidate capabilities..."
        )
        result = await self._ask_agent_sdk(
            PROMPT_AGENTIC_ANALYSIS_PLAN_SYSTEM,
            PROMPT_AGENTIC_ANALYSIS_PLAN_USER.format(
                kit_name=kit_name,
                project_paths=project_paths,
                plan_source_hints=plan_source_hints,
                plan_source_context=plan_source_context,
                capability_summary=_format_agentic_plan_inventory(inventory),
                risk_policy=json.dumps(rule_context.get("risk_policy", {}), indent=2),
            ),
            cwd=str(cwd) if cwd else None,
            timeout=_bounded_agent_timeout(self.agent_plan_timeout, self.timeout),
            tools=[],
            max_turns=_env_int("AGENTBRIDGE_AGENT_PLAN_MAX_TURNS", 4),
        )
        parsed = _parse_json_object(result, {})
        if not parsed:
            raise RuntimeError(_invalid_generation_json_message(result))
        self._progress("Received Claude Agent SDK project understanding plan.")
        return parsed

    def _build_agentic_plan_source_context(self, plan_source_hints: str, input_paths: list[Path]) -> str:
        roots = [path.resolve() for path in input_paths if path.is_dir()]
        sections: list[str] = []
        total_chars = 0
        for line in plan_source_hints.splitlines():
            label = line.strip().removeprefix("-").strip()
            if not label or label == "<none>":
                continue
            path = Path(label)
            if not path.is_absolute() and roots:
                path = roots[0] / path
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            excerpt = content[:4_000]
            sections.append(f"### {label}\n{excerpt}")
            total_chars += len(excerpt)
            self._progress(f"Added high-signal source excerpt for SDK planning: {label}")
            if total_chars >= 18_000:
                break
        return "\n\n".join(sections) or "<no source excerpts available>"

    def _result_from_generation_text(
        self,
        capabilities: list[Capability],
        rule_context: dict[str, Any],
        result: str,
    ) -> dict[str, Any]:
        caps_data = [cap.to_dict() for cap in capabilities]
        parsed = _parse_json_object(result, {})
        if not parsed:
            raise RuntimeError(_invalid_generation_json_message(result))
        self._progress("Received AI analysis response; parsing generated kit metadata...")

        enhanced_caps = self._apply_enhanced_capabilities(capabilities, parsed)
        system_prompt = parsed.get("system_prompt", "")
        skills = parsed.get("skills", {})

        return {
            "enhanced_capabilities": enhanced_caps,
            "agent_analysis": normalize_agent_analysis(parsed),
            "rule_signals": {
                "candidate_capabilities": caps_data,
                **rule_context,
            },
            "system_prompt": system_prompt,
            "skills": skills,
        }

    def _apply_enhanced_capabilities(
        self, capabilities: list[Capability], parsed: dict[str, Any]
    ) -> list[Capability]:
        tool_enhancements = parsed.get("tool_enhancements", {})
        risk_assessments = parsed.get("risk_assessments", {})
        additional_tools = parsed.get("additional_tools", [])

        for cap in capabilities:
            enh = tool_enhancements.get(cap.name, {})
            if enh.get("description"):
                cap.description = enh["description"]
            if enh.get("when_to_use"):
                cap.description = f"{cap.description} Use when: {enh['when_to_use']}"
            if enh.get("caveats"):
                cap.description = f"{cap.description} Caveats: {enh['caveats']}"

            risk_info = risk_assessments.get(cap.name, {})
            if risk_info.get("risk") in ("read", "write", "destructive", "external_side_effect"):
                cap.risk = risk_info["risk"]
                cap.confirm_required = confirmation_required(risk_info["risk"])

        for tool_def in additional_tools:
            if isinstance(tool_def, dict) and tool_def.get("name"):
                from agentbridge.models import SourceRef
                risk = tool_def.get("risk", "read")
                cap = Capability(
                    name=tool_def["name"],
                    domain=tool_def.get("domain", "inferred"),
                    resource=tool_def.get("resource", "inferred"),
                    action=tool_def.get("action", "run"),
                    description=tool_def.get("description", tool_def["name"]),
                    input_schema=tool_def.get("input_schema", {"type": "object", "properties": {}}),
                    risk=risk,
                    confirm_required=confirmation_required(risk),
                    source=SourceRef("ai_inferred", "", tool_def.get("rationale", "")),
                    transport={"type": "inferred"},
                    dry_run_supported=True,
                )
                capabilities.append(cap)

        return capabilities

    async def _ask_agent_sdk(
        self,
        system_prompt: str,
        user_prompt: str,
        cwd: str | None = None,
        timeout: float | None = None,
        tools: list[str] | None = None,
        max_turns: int | None = None,
    ) -> str:
        effective_timeout = timeout if timeout is not None else self.timeout
        result_queue: queue.Queue[tuple[str, str | Exception]] = queue.Queue(maxsize=1)
        cancelled = threading.Event()

        def _worker() -> None:
            try:
                result = asyncio.run(
                    self._collect_agent_sdk_response(
                        system_prompt,
                        user_prompt,
                        cwd,
                        cancelled,
                        tools=tools,
                        max_turns=max_turns,
                    )
                )
            except Exception as exc:
                result_queue.put(("error", exc))
            else:
                result_queue.put(("ok", result))

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        try:
            status, value = result_queue.get(timeout=effective_timeout)
        except queue.Empty as exc:
            cancelled.set()
            raise RuntimeError(
                f"Claude Agent SDK request timed out after {effective_timeout:g} seconds. "
                "Try --batch-size 3, increase --llm-timeout, or use --analysis-mode prompt."
            ) from exc
        if status == "error":
            raise value if isinstance(value, Exception) else RuntimeError(str(value))
        return str(value)

    async def _collect_agent_sdk_response(
        self,
        system_prompt: str,
        user_prompt: str,
        cwd: str | None = None,
        cancelled: threading.Event | None = None,
        tools: list[str] | None = None,
        max_turns: int | None = None,
    ) -> str:
        from claude_agent_sdk import ClaudeAgentOptions
        from claude_agent_sdk import query

        if cancelled is not None and cancelled.is_set():
            return ""
        read_only_tools = tools or ["Read", "Grep", "Glob", "LS"]
        options_kwargs: dict[str, Any] = {
            "system_prompt": system_prompt,
            "max_turns": max_turns or _env_int("AGENTBRIDGE_AGENT_MAX_TURNS", 12),
            "model": None if self.base_url else self.model,
            "tools": read_only_tools,
            "allowed_tools": read_only_tools,
            "disallowed_tools": ["Write", "Edit", "NotebookEdit", "Bash", "Agent"],
            "load_timeout_ms": _env_int("AGENTBRIDGE_AGENT_LOAD_TIMEOUT_MS", 10000),
        }
        env = {
            "ANTHROPIC_API_KEY": self.api_key,
            "PYDANTIC_DISABLE_PLUGINS": os.environ.get("PYDANTIC_DISABLE_PLUGINS", "__all__"),
        }
        if self.base_url:
            env["ANTHROPIC_BASE_URL"] = self.base_url
        if self.model:
            env["ANTHROPIC_MODEL"] = self.model
        options_kwargs["env"] = env
        if self.base_url:
            options_kwargs["base_url"] = self.base_url
            options_kwargs["settings"] = _agent_sdk_settings(self.base_url, self.model)
        if cwd:
            options_kwargs["cwd"] = cwd
        options = _construct_with_supported_kwargs(ClaudeAgentOptions, options_kwargs)
        collected: list[str] = []
        location = f" with cwd={cwd}" if cwd else ""
        endpoint = f" via {self.base_url}" if self.base_url else ""
        self._progress(f"Calling Claude Agent SDK query{location}{endpoint}; waiting for streamed agent events...")
        async for message in query(prompt=user_prompt, options=options):
            if cancelled is not None and cancelled.is_set():
                return ""
            self._report_agent_sdk_message(message)
            collected.extend(_extract_assistant_texts(message))
        return "".join(collected)

    async def _ask(self, system_prompt: str, user_prompt: str) -> str:
        if self._backend == self._BACKEND_AGENT_SDK:
            return await self._ask_agent_sdk(system_prompt, user_prompt)
        return await asyncio.to_thread(self._ask_anthropic_sync, system_prompt, user_prompt)

    def _report_agent_sdk_message(self, message: Any) -> None:
        for event in _agent_sdk_progress_events(message):
            self._progress(event)

    def _ask_anthropic_sync(self, system_prompt: str, user_prompt: str) -> str:
        import anthropic

        kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        client = anthropic.Anthropic(**kwargs)
        endpoint = self.base_url or "Anthropic default endpoint"
        self._progress(f"Calling Anthropic Messages API at {endpoint} with model {self.model}.")
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=8192,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                timeout=self.timeout,
            )
        except Exception as exc:
            if _is_timeout_error(exc):
                raise RuntimeError(
                    f"LLM request timed out after {self.timeout:g} seconds. "
                    "Try a faster model, increase --llm-timeout, or run with --no-ai for deterministic generation."
                ) from exc
            raise
        collected: list[str] = []
        for block in response.content:
            if getattr(block, "type", "") == "text":
                collected.append(getattr(block, "text", ""))
        return "".join(collected)


class AgentRunner:
    def __init__(
        self,
        kit_dir: str | Path,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        target_base_url: str = "",
        headers: dict[str, str] | None = None,
        execute: bool = False,
        timeout: float = 30.0,
        read_only: bool = False,
        deny_risks: set[str] | None = None,
        allow_tools: set[str] | None = None,
        audit_log: Path | None = None,
        session_id: str = "default",
        llm_timeout: float | None = None,
        graphql_endpoint: str = "",
        database_url: str = "",
        grpc_target: str = "",
    ) -> None:
        self.kit_dir = Path(kit_dir)
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "LLM API key is required for agent sessions. "
                "Set ANTHROPIC_API_KEY environment variable or pass api_key parameter."
            )
        self.base_url = base_url or os.environ.get("ANTHROPIC_BASE_URL", "")
        self.model = _resolve_agent_runner_model(model)
        self.session_id = session_id or "default"
        self._stable_sdk_session_id = _agent_sdk_session_id(self.kit_dir, self.session_id)
        self.sdk_session_id = _temporary_sdk_session_id(self._stable_sdk_session_id)
        self.llm_timeout = llm_timeout

        os.environ.setdefault("ANTHROPIC_API_KEY", self.api_key)
        if self.base_url:
            os.environ.setdefault("ANTHROPIC_BASE_URL", self.base_url)

        self._capabilities: dict[str, dict[str, Any]] = {}
        self._system_prompt = ""
        self.last_usage: dict[str, Any] = {}
        self._load_kit()
        self._system_prompt = _with_runtime_auth_guidance(self._system_prompt)
        from agentbridge.mcp_server import AgentBridgeMCPServer, MCPServerConfig

        self._server = AgentBridgeMCPServer(
            MCPServerConfig(
                kit_dir=self.kit_dir,
                base_url=target_base_url,
                headers=headers if headers is not None else {},
                execute=execute,
                timeout=timeout,
                read_only=read_only,
                deny_risks=deny_risks or set(),
                allow_tools=allow_tools or set(),
                audit_log=audit_log,
                graphql_endpoint=graphql_endpoint,
                database_url=database_url,
                grpc_target=grpc_target,
            )
        )
        self._client: Any | None = None
        self._client_loop: asyncio.AbstractEventLoop | None = None
        self._client_thread: threading.Thread | None = None
        self._client_lock = threading.Lock()
        self._query_lock = threading.Lock()
        self._permission_lock = threading.Lock()
        self._pending_permission: dict[str, Any] | None = None
        self._permission_events: queue.Queue[tuple[str, Any]] | None = None
        self.stream_idle_timeout = (
            llm_timeout if llm_timeout is not None else _env_float("AGENTBRIDGE_AGENT_STREAM_IDLE_TIMEOUT", 45.0)
        )

    def _load_kit(self) -> None:
        caps_path = self.kit_dir / "capabilities.json"
        if caps_path.exists():
            data = json.loads(caps_path.read_text(encoding="utf-8"))
            self._capabilities = {item["name"]: item for item in data}
        prompt_path = self.kit_dir / "prompts" / "system.md"
        if prompt_path.exists():
            self._system_prompt = prompt_path.read_text(encoding="utf-8")

    async def query(self, prompt: str) -> Any:
        messages = await asyncio.to_thread(self.query_messages, prompt)
        for msg in messages:
            yield msg

    def query_messages(self, prompt: str) -> list[Any]:
        with self._query_lock:
            loop = self._ensure_client_loop()
            future = asyncio.run_coroutine_threadsafe(self._query_messages_async(prompt), loop)
            return future.result(timeout=self.llm_timeout)

    def stream_messages(self, prompt: str) -> Any:
        with self._query_lock:
            loop = self._ensure_client_loop()
            events: queue.Queue[tuple[str, Any]] = queue.Queue()
            self._permission_events = events
            stream_future = asyncio.run_coroutine_threadsafe(self._stream_messages_async(prompt, events), loop)
            idle_timeout = self.stream_idle_timeout if self.stream_idle_timeout and self.stream_idle_timeout > 0 else None
            last_stream_event_at = time.monotonic()
            try:
                while True:
                    try:
                        kind, payload = events.get(timeout=0.25 if idle_timeout else None)
                    except queue.Empty as exc:
                        if self._has_pending_permission():
                            last_stream_event_at = time.monotonic()
                            continue
                        if not idle_timeout or time.monotonic() - last_stream_event_at < idle_timeout:
                            continue
                        self._abort_active_stream()
                        try:
                            stream_future.result(timeout=1)
                        except Exception:
                            pass
                        raise TimeoutError(
                            f"Claude Agent SDK stream produced no events for {idle_timeout:g} seconds."
                        ) from exc
                    last_stream_event_at = time.monotonic()
                    if kind == "message":
                        yield payload
                    elif kind == "error":
                        raise payload
                    elif kind == "done":
                        return
            finally:
                self._permission_events = None

    def _has_pending_permission(self) -> bool:
        with self._permission_lock:
            return self._pending_permission is not None

    def _abort_active_stream(self) -> None:
        try:
            self.interrupt()
        except Exception:
            pass
        loop = self._client_loop
        if loop is None:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self._reset_client_async(), loop)
            future.result(timeout=5)
        except Exception:
            pass

    def resolve_permission(self, request_id: str, allow: bool) -> bool:
        with self._permission_lock:
            pending = self._pending_permission
            if not pending or pending.get("id") != request_id:
                return False
            pending["allow"] = allow
            event = pending.get("event")
            if isinstance(event, threading.Event):
                event.set()
            return True

    def interrupt(self) -> None:
        with self._permission_lock:
            pending = self._pending_permission
            if pending:
                pending["allow"] = False
                event = pending.get("event")
                if isinstance(event, threading.Event):
                    event.set()
        loop = self._client_loop
        client = self._client
        if loop is None or client is None:
            return
        interrupt = getattr(client, "interrupt", None)
        if not callable(interrupt):
            return
        future = asyncio.run_coroutine_threadsafe(interrupt(), loop)
        future.result(timeout=5)

    def query_text(self, prompt: str) -> str:
        if "query" in self.__dict__:
            return _run_async(self._query_text_async(prompt))
        messages = self.query_messages(prompt)
        return self._messages_to_text(messages)

    def close(self) -> None:
        loop = self._client_loop
        if loop is None:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self._close_client_async(), loop)
            future.result(timeout=5)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            if self._client_thread is not None:
                self._client_thread.join(timeout=5)
            self._client_loop = None
            self._client_thread = None

    def _ensure_client_loop(self) -> asyncio.AbstractEventLoop:
        with self._client_lock:
            if self._client_loop is not None and self._client_loop.is_running():
                return self._client_loop
            ready: queue.Queue[asyncio.AbstractEventLoop] = queue.Queue(maxsize=1)

            def run_loop() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                ready.put(loop)
                try:
                    loop.run_forever()
                finally:
                    loop.close()

            thread = threading.Thread(target=run_loop, name=f"agentbridge-agent-{self.sdk_session_id}", daemon=True)
            thread.start()
            self._client_loop = ready.get()
            self._client_thread = thread
            return self._client_loop

    async def _query_messages_async(self, prompt: str) -> list[Any]:
        try:
            return await self._query_messages_once_async(prompt)
        except Exception as exc:
            if not _is_agent_session_retryable_error(exc):
                raise
            await self._reset_client_async()
            self.sdk_session_id = _temporary_sdk_session_id(self._stable_sdk_session_id)
            return await self._query_messages_once_async(prompt)

    async def _query_messages_once_async(self, prompt: str) -> list[Any]:
        client = await self._ensure_client_async()
        await client.query(prompt)
        return [msg async for msg in client.receive_response()]

    async def _stream_messages_async(self, prompt: str, events: queue.Queue[tuple[str, Any]]) -> None:
        try:
            async for msg in self._stream_messages_once_async(prompt):
                if _is_agent_stream_event(msg) and not _extract_agent_stream_text_delta(msg):
                    continue
                events.put(("message", msg))
        except Exception as exc:
            if not _is_agent_session_retryable_error(exc):
                events.put(("error", exc))
                events.put(("done", None))
                return
            try:
                await self._reset_client_async()
                self.sdk_session_id = _temporary_sdk_session_id(self._stable_sdk_session_id)
                async for msg in self._stream_messages_once_async(prompt):
                    if _is_agent_stream_event(msg) and not _extract_agent_stream_text_delta(msg):
                        continue
                    events.put(("message", msg))
            except Exception as retry_exc:
                events.put(("error", retry_exc))
        finally:
            events.put(("done", None))

    async def _stream_messages_once_async(self, prompt: str) -> Any:
        client = await self._ensure_client_async()
        await client.query(prompt)
        async for msg in client.receive_response():
            yield msg

    async def _ensure_client_async(self) -> Any:
        if self._client is not None:
            return self._client
        import claude_agent_sdk as sdk_module
        from claude_agent_sdk import (
            ClaudeAgentOptions,
            ClaudeSDKClient,
            create_sdk_mcp_server,
            tool,
        )

        kit_tools = self._build_kit_tools(tool)
        server = create_sdk_mcp_server(
            name="agentbridge-kit",
            version="1.0.0",
            tools=kit_tools,
        )
        allowed = [f"mcp__agentbridge-kit__{name}" for name in self._capabilities]
        sdk_env = {
            "ANTHROPIC_API_KEY": self.api_key,
            "ANTHROPIC_MODEL": self.model,
            **({"ANTHROPIC_BASE_URL": self.base_url} if self.base_url else {}),
        }
        sdk_settings = _agent_sdk_settings(self.base_url, self.model)

        async def can_use_tool(tool_name: str, tool_input: dict[str, Any], context: Any) -> Any:
            if _is_read_only_permission_request(tool_name, tool_input):
                return sdk_module.PermissionResultAllow()
            request_id = str(uuid.uuid4())[:8]
            decision = threading.Event()
            operation = _summarize_permission_operation(tool_name, tool_input, context)
            pending = {
                "id": request_id,
                "tool": tool_name,
                "input": tool_input,
                "title": getattr(context, "title", None) or f"Authorize {tool_name}",
                "display_name": getattr(context, "display_name", None) or tool_name,
                "description": getattr(context, "description", None) or getattr(context, "decision_reason", None) or "",
                "operation": operation,
                "tool_use_id": getattr(context, "tool_use_id", None),
                "event": decision,
                "allow": None,
            }
            payload = {key: value for key, value in pending.items() if key not in {"event", "allow"} and value is not None}
            with self._permission_lock:
                self._pending_permission = pending
            if self._permission_events is not None:
                self._permission_events.put(("message", {"type": "agent_permission_required", "pending": payload}))
            await asyncio.to_thread(decision.wait)
            with self._permission_lock:
                allow = bool(pending.get("allow"))
                if self._pending_permission is pending:
                    self._pending_permission = None
            if allow:
                return sdk_module.PermissionResultAllow()
            return sdk_module.PermissionResultDeny(message="Denied by user.")

        async def connect_with_session(session_id: str) -> Any:
            options = _construct_with_supported_kwargs(
                ClaudeAgentOptions,
                {
                    "system_prompt": self._system_prompt,
                    "mcp_servers": {"agentbridge-kit": server},
                    "allowed_tools": allowed,
                    "session_id": session_id,
                    "model": None if self.base_url else self.model,
                    "base_url": self.base_url or None,
                    "env": sdk_env,
                    "include_partial_messages": True,
                    "can_use_tool": can_use_tool,
                    **({"settings": sdk_settings} if sdk_settings else {}),
                },
            )
            client = ClaudeSDKClient(options=options)
            self._client = client
            try:
                await client.connect()
            except Exception:
                try:
                    await client.disconnect()
                except Exception:
                    pass
                self._client = None
                raise
            return client

        try:
            return await connect_with_session(self.sdk_session_id)
        except Exception as exc:
            if not _is_agent_session_retryable_error(exc):
                raise
            self.sdk_session_id = _temporary_sdk_session_id(self._stable_sdk_session_id)
            return await connect_with_session(self.sdk_session_id)

    async def _close_client_async(self) -> None:
        await self._reset_client_async()

    async def _reset_client_async(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.disconnect()
        finally:
            self._client = None

    async def _query_text_async(self, prompt: str) -> str:
        messages = [msg async for msg in self.query(prompt)]
        return self._messages_to_text(messages)

    def _messages_to_text(self, messages: list[Any]) -> str:
        content_chunks: list[str] = []
        result_chunks: list[str] = []
        usage: dict[str, Any] = {}
        for msg in messages:
            message_usage = _extract_agent_usage(msg)
            if message_usage:
                usage = message_usage
            result_text = _extract_agent_result_text(msg)
            if result_text:
                result_chunks.append(result_text)
                continue
            text = _extract_agent_message_text(msg)
            if text:
                content_chunks.append(text)
        self.last_usage = usage
        return "\n".join(result_chunks or content_chunks).strip()

    def _build_kit_tools(self, tool_decorator: Any) -> list[Any]:
        tools: list[Any] = []
        for name, cap in self._capabilities.items():
            schema = cap.get("input_schema", {})
            properties = schema.get("properties", {})
            param_types: dict[str, type] = {}
            for key, value in properties.items():
                typ = value.get("type", "string") if isinstance(value, dict) else "string"
                if typ in ("number", "integer"):
                    param_types[key] = float
                elif typ == "boolean":
                    param_types[key] = bool
                else:
                    param_types[key] = str

            cap_name = name
            cap_desc = cap.get("description", name)

            async def _handler(args: dict, _name: str = cap_name) -> dict[str, Any]:
                response = self._server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": _name,
                        "method": "tools/call",
                        "params": {"name": _name, "arguments": args or {}},
                    }
                )
                if not response:
                    text = json.dumps({"tool": _name, "error": "No MCP response"})
                    return {"content": [{"type": "text", "text": text}], "isError": True}
                if "error" in response:
                    text = json.dumps({"tool": _name, "error": response["error"].get("message", "Tool call failed")})
                    return {"content": [{"type": "text", "text": text}], "isError": True}
                return response.get("result", {"content": [{"type": "text", "text": "{}"}]})

            t = tool_decorator(name, cap_desc, param_types)(_handler)
            tools.append(t)
        return tools


def _extract_agent_message_text(message: Any) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        content = message.get("content", [])
    else:
        content = getattr(message, "content", [])
    if isinstance(content, str):
        return content
    chunks: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    chunks.append(str(block["text"]))
            elif getattr(block, "type", "") == "text" and getattr(block, "text", ""):
                chunks.append(str(getattr(block, "text")))
    return "\n".join(chunks).strip()


def _is_agent_stream_event(message: Any) -> bool:
    if isinstance(message, dict):
        return message.get("type") == "stream_event" or isinstance(message.get("event"), dict)
    return message.__class__.__name__ == "StreamEvent" or isinstance(getattr(message, "event", None), dict)


def _extract_agent_stream_text_delta(message: Any) -> str:
    event = message.get("event") if isinstance(message, dict) else getattr(message, "event", None)
    if not isinstance(event, dict):
        return ""
    if event.get("type") == "content_block_delta":
        delta = event.get("delta")
        if isinstance(delta, dict):
            text = delta.get("text")
            return text if isinstance(text, str) else ""
    if event.get("type") == "content_block_start":
        content_block = event.get("content_block")
        if isinstance(content_block, dict) and content_block.get("type") == "text":
            text = content_block.get("text")
            return text if isinstance(text, str) else ""
    return ""


def _is_read_only_permission_request(tool_name: str, tool_input: dict[str, Any]) -> bool:
    name = tool_name.lower()
    if name in {"read", "grep", "glob", "ls"}:
        return True
    if name != "bash":
        return False
    command = str(tool_input.get("command", "") or "")
    lowered = command.lower()
    mutating_markers = [
        " -x post",
        " -x put",
        " -x patch",
        " -x delete",
        "--request post",
        "--request put",
        "--request patch",
        "--request delete",
        " >",
        ">>",
        " rm ",
        " mv ",
        " cp ",
        " chmod ",
        " chown ",
        " mkdir ",
        " touch ",
        " python -c",
        " node -e",
    ]
    if any(marker in f" {lowered} " for marker in mutating_markers):
        return False
    if any(marker in lowered for marker in ["/auth/login", "/login", "signin", "sign_in"]):
        return False
    if "curl " in lowered and not any(method in lowered for method in ["post", "put", "patch", "delete"]):
        return True
    return _is_read_only_local_shell_command(command)


def _is_read_only_local_shell_command(command: str) -> bool:
    normalized = re.sub(r"\s*2>\s*/dev/null", "", command).strip()
    if not normalized:
        return False
    lowered = normalized.lower()
    if re.search(r"(^|[;&|]\s*)(sudo|rm|mv|cp|chmod|chown|mkdir|touch|tee|python\s+-c|python3\s+-c|node\s+-e)\b", lowered):
        return False
    if re.search(r"(^|[^0-9])>>?", normalized):
        return False
    parts = [part.strip() for part in re.split(r"\s*(?:&&|\|\||;|\|)\s*", normalized) if part.strip()]
    if not parts:
        return False
    allowed_commands = {"cat", "echo", "ls", "find", "grep", "rg", "head", "tail", "pwd", "wc", "jq"}
    for part in parts:
        try:
            tokens = shlex.split(part)
        except ValueError:
            return False
        if not tokens:
            return False
        executable = Path(tokens[0]).name.lower()
        if executable in allowed_commands:
            continue
        if executable in {"python", "python3"} and tokens[1:4] == ["-m", "json.tool"]:
            continue
        if executable == "sed" and len(tokens) > 1 and tokens[1] == "-n":
            continue
        return False
    return True


def _summarize_permission_operation(tool_name: str, tool_input: dict[str, Any], context: Any) -> str:
    command = str(tool_input.get("command", "") or "") if isinstance(tool_input, dict) else ""
    description = str(getattr(context, "description", "") or getattr(context, "decision_reason", "") or "")
    haystack = f"{command}\n{description}".lower()
    if any(marker in haystack for marker in ["/auth/login", "/login", "signin", "sign_in"]):
        return "Login"
    method = _curl_method(command)
    path = _first_url_path(command)
    if path:
        resource = _resource_from_path(path)
        if method == "GET":
            return f"Get {resource} detail" if _path_has_identifier(path) else f"List {resource}"
        if method == "POST":
            return f"Create {resource}"
        if method == "PATCH" or method == "PUT":
            return f"Update {resource}"
        if method == "DELETE":
            return f"Delete {resource}"
    title = str(getattr(context, "title", "") or "")
    if title and not title.lower().startswith("authorize "):
        return title
    return _humanize_identifier(tool_name)


def _curl_method(command: str) -> str:
    lowered = command.lower()
    method_match = re.search(r"(?:-x|--request)\s+([a-z]+)", lowered)
    if method_match:
        return method_match.group(1).upper()
    if re.search(r"\s-d\s|--data(?:-raw|-binary|-urlencode)?\s", lowered):
        return "POST"
    return "GET"


def _first_url_path(command: str) -> str:
    match = re.search(r"https?://[^'\"\s\\]+", command)
    if not match:
        return ""
    try:
        from urllib.parse import urlparse

        return urlparse(match.group(0)).path
    except Exception:
        return ""


def _path_has_identifier(path: str) -> bool:
    parts = [part for part in path.strip("/").split("/") if part]
    return bool(parts and re.search(r"\d|cm[a-z0-9]{6,}|[a-f0-9-]{12,}", parts[-1], re.I))


def _resource_from_path(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    filtered = [part for part in parts if part.lower() not in {"api", "v1", "v2", "v3"}]
    resource = filtered[-2] if filtered and _path_has_identifier(path) and len(filtered) >= 2 else (filtered[-1] if filtered else "operation")
    resource = re.sub(r"[-_]+", " ", resource).strip()
    if resource.endswith("ies"):
        resource = resource[:-3] + "y"
    elif resource.endswith("s") and len(resource) > 3:
        resource = resource[:-1]
    return resource or "operation"


def _humanize_identifier(value: str) -> str:
    words = re.sub(r"[_-]+", " ", value).strip()
    return words[:1].upper() + words[1:] if words else "Agent operation"


def _with_runtime_auth_guidance(system_prompt: str) -> str:
    guidance = (
        "Runtime authentication guidance:\n"
        "- If a target API response is HTTP 401, code 100002, or says Token expired, treat the saved token as expired.\n"
        "- Refresh authentication first: use the AgentBridge kit login tool, which can reuse the selected saved account, then retry the original operation once.\n"
        "- Do not keep retrying the same expired token with Bash/curl. If refresh is unavailable or still fails, tell the user the token expired and ask them to select a saved account or login again."
    )
    prompt = system_prompt.strip()
    if guidance in prompt:
        return prompt
    return (prompt + "\n\n" + guidance).strip() if prompt else guidance


def _extract_agent_result_text(message: Any) -> str:
    result = message.get("result") if isinstance(message, dict) else getattr(message, "result", None)
    return str(result).strip() if result else ""


def _extract_agent_usage(message: Any) -> dict[str, Any]:
    def get_value(name: str, default: Any = None) -> Any:
        if isinstance(message, dict):
            return message.get(name, default)
        return getattr(message, name, default)

    raw_usage = get_value("usage", {})
    if not isinstance(raw_usage, dict):
        raw_usage = {
            key: getattr(raw_usage, key)
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            )
            if hasattr(raw_usage, key)
        }
    input_tokens = int(raw_usage.get("input_tokens", 0) or 0)
    output_tokens = int(raw_usage.get("output_tokens", 0) or 0)
    if not raw_usage and not any(
        get_value(name) is not None
        for name in ("total_cost_usd", "duration_ms", "num_turns")
    ):
        return {}
    usage: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
    for key in ("cache_read_input_tokens", "cache_creation_input_tokens"):
        value = int(raw_usage.get(key, 0) or 0)
        if value:
            usage[key] = value
    cost = get_value("total_cost_usd")
    if cost is not None:
        usage["cost_usd"] = float(cost)
    duration = get_value("duration_ms")
    if duration is not None:
        usage["duration_ms"] = int(duration)
    turns = get_value("num_turns")
    if turns is not None:
        usage["turns"] = int(turns)
    return usage


def _is_agent_session_in_use_error(exc: Exception) -> bool:
    stderr = getattr(exc, "stderr", None)
    if isinstance(stderr, bytes):
        stderr_text = stderr.decode("utf-8", errors="replace")
    else:
        stderr_text = str(stderr or "")
    text = f"{exc}\n{stderr_text}".lower()
    return "session id" in text and "already in use" in text


def _is_agent_session_retryable_error(exc: Exception) -> bool:
    if _is_agent_session_in_use_error(exc):
        return True
    stderr = getattr(exc, "stderr", None)
    if isinstance(stderr, bytes):
        stderr_text = stderr.decode("utf-8", errors="replace")
    else:
        stderr_text = str(stderr or "")
    text = f"{exc}\n{stderr_text}".lower()
    return "check stderr output for details" in text and (
        "message reader" in text or "command failed" in text
    )


def _temporary_sdk_session_id(stable_session_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"agentbridge:{stable_session_id}:fallback:{uuid.uuid4()}"))


def _resolve_agent_runner_model(model: str | None) -> str:
    if model:
        return model
    env_model = os.environ.get("ANTHROPIC_MODEL", "").strip()
    if env_model:
        return env_model
    return _DEFAULT_MODEL


def _agent_sdk_settings(base_url: str, model: str) -> str:
    if not base_url:
        return ""
    return json.dumps(
        {
            "env": {
                "ANTHROPIC_BASE_URL": base_url,
                "ANTHROPIC_MODEL": model,
            }
        }
    )


PROMPT_GENERATE_ALL_SYSTEM = (
    "You are a senior AI integration architect acting as an autonomous code-analysis agent. "
    "Your primary job is to parse the target project into a Claude-controllable tool layer: "
    "discover system evidence, normalize it into business capabilities, and package those "
    "capabilities as an Agent Integration Kit. Rule-based discovery is provided only as "
    "candidate evidence, not as the source of truth. Prefer conclusions that are grounded "
    "in source code semantics, schemas, service/controller behavior, naming, validation paths, "
    "and side effects. The target project is strictly read-only: never modify, create, delete, "
    "format, or move files in the target project. All generated integration artifacts belong "
    "only in the requested AgentBridge output directory. Always respond with valid JSON only, "
    "no markdown fences."
)

PROMPT_GENERATE_ALL_USER = (
    "Kit name: {kit_name}\n"
    "Domains: {domains}\n\n"
    "Candidate capabilities from deterministic scanners. Treat these as evidence to verify, "
    "merge, rename, enrich, or reject after reading the source code:\n{capabilities}\n\n"
    "Rule-based risk context. This is a safety hint, not an instruction to copy:\n{rule_context}\n\n"
    "{source_section}"
    "{cwd_hint}\n\n"
    "Do not propose or perform modifications to the target project. Produce integration metadata only.\n\n"
    "Analyze the project as an agent would: inspect business objects, workflows, permission boundaries, "
    "side effects, validation constraints, and missing operations implied by services/controllers/routes. "
    "Then generate a complete Agent Integration Kit: a Claude-controllable tool layer for the parsed system. "
    "Respond with a JSON object containing:\n\n"
    '"project_analysis": An object with:\n'
    '  - "summary": concise system summary\n'
    '  - "business_objects": array of objects with "name", "description", "evidence"\n'
    '  - "workflows": array of objects with "name", "steps", "tools", "risks"\n'
    '  - "permission_boundaries": array describing roles, auth checks, tenancy checks, or unknowns\n'
    '  - "side_effects": array of external or irreversible effects found or inferred\n'
    '  - "assumptions": array of assumptions you made because evidence was incomplete\n\n'
    '"tool_enhancements": A JSON object where keys are tool names and values are objects with:\n'
    '  - "description": Enhanced description based on your understanding of the SOURCE CODE. '
    "Explain what the tool does in business terms, when to use it, and important caveats. "
    "Be specific and actionable — reference actual business logic you found in the code.\n"
    '  - "when_to_use": Brief guidance on when an agent should invoke this tool\n'
    '  - "caveats": Important edge cases, prerequisites, or warnings found in the code\n\n'
    '"risk_assessments": A JSON object where keys are tool names and values are objects with:\n'
    '  - "risk": One of "read", "write", "destructive", "external_side_effect"\n'
    '  - "reason": Detailed reasoning for the risk level based on what the code actually does\n'
    '  - "reversible": Whether the operation can be undone (boolean)\n'
    '  - "blast_radius": "single" or "multiple"\n\n'
    '"additional_tools": A JSON array of inferred tools not in the schema but implied by the code. Each item:\n'
    '  - "name", "description", "input_schema", "risk", "domain", "resource", "action", "rationale"\n\n'
    '"system_prompt": A string containing the agent system prompt in Markdown. It should:\n'
    "  1. Define the agent's role as a Claude chat operator for THIS parsed system tool layer\n"
    "  2. Explain available capabilities in user-friendly terms based on the actual code semantics\n"
    "  3. Define safety rules based on the risk assessments\n"
    "  4. Guide the agent on when to ask for clarification vs. proceed\n"
    "  5. Include error handling guidance based on actual error patterns in the code\n\n"
    '"skills": A JSON object where keys are domain names and values are Markdown skill documents. Each should:\n'
    "  1. Describe when to activate this skill\n"
    "  2. Provide step-by-step workflows for common operations IN THIS DOMAIN based on the actual code\n"
    "  3. Include error handling and edge cases specific to this domain found in the code\n"
    "  4. List best practices for this domain based on code patterns you observed\n"
    "  5. Reference the relevant tools by name\n"
)

PROMPT_GENERATE_ALL_AGENTIC_USER = (
    "Kit name: {kit_name}\n"
    "Domains: {domains}\n\n"
    "Project paths to inspect read-only:\n{project_paths}\n\n"
    "High-signal files/locations for this batch. Prefer these exact files before any broader search:\n"
    "{source_hints}\n\n"
    "{guidance_section}"
    "Candidate capabilities from deterministic scanners. Treat these as evidence to verify, "
    "merge, rename, enrich, or reject after reading the source code:\n{capabilities}\n\n"
    "Rule-based risk context. This is a safety hint, not an instruction to copy:\n{rule_context}\n\n"
    "Use only Read and Grep. Do not use Glob, LS, Bash, sub-agents, or full-repository exploration. "
    "First read the exact high-signal files listed above. If evidence is still missing, use at most "
    "two focused Grep searches whose patterns are exact capability names, controller names, route paths, "
    "or DTO/model names from this prompt. Keep inspection quick and return JSON in this same turn. "
    "If a candidate cannot be verified from source, keep it conservative and list the uncertainty in assumptions.\n\n"
    "You are enhancing only this batch of candidate capabilities. Preserve stable tool names unless "
    "there is strong evidence that a rename is necessary. Infer additional tools only when source code "
    "clearly exposes an operation that scanner evidence missed.\n\n"
    "After the focused inspection, respond with one JSON object only, no markdown fences. "
    "Keep arrays concise: no more than 5 business objects, 5 workflows, 6 permission boundaries, "
    "6 side effects, and 8 assumptions for this batch. The object must contain:\n\n"
    '"project_analysis": An object with:\n'
    '  - "summary": concise system summary\n'
    '  - "business_objects": array of objects with "name", "description", "evidence"\n'
    '  - "workflows": array of objects with "name", "steps", "tools", "risks"\n'
    '  - "permission_boundaries": array describing roles, auth checks, tenancy checks, or unknowns\n'
    '  - "side_effects": array of external or irreversible effects found or inferred\n'
    '  - "assumptions": array of assumptions you made because evidence was incomplete\n\n'
    '"tool_enhancements": A JSON object where keys are tool names and values are objects with:\n'
    '  - "description": Enhanced business description grounded in source evidence\n'
    '  - "when_to_use": Brief guidance on when an agent should invoke this tool\n'
    '  - "caveats": Important edge cases, prerequisites, or warnings found in the code\n\n'
    '"risk_assessments": A JSON object where keys are tool names and values are objects with:\n'
    '  - "risk": One of "read", "write", "destructive", "external_side_effect"\n'
    '  - "reason": Detailed reasoning for the risk level based on what the code actually does\n'
    '  - "reversible": Whether the operation can be undone (boolean)\n'
    '  - "blast_radius": "single" or "multiple"\n\n'
    '"additional_tools": A JSON array. Prefer [] for batch mode unless an adjacent source file clearly exposes '
    'a missing operation. Each item, if any, has "name", "description", "input_schema", "risk", '
    '"domain", "resource", "action", "rationale"\n\n'
    '"system_prompt": A short Markdown string, 1-3 paragraphs maximum, summarizing only this batch context.\n'
    '"skills": A JSON object. Prefer {{}} for batch mode unless a concise domain note is necessary.\n'
)

PROMPT_AGENTIC_ANALYSIS_PLAN_SYSTEM = (
    "You are the lead project-understanding agent for AgentBridge. Inspect the target project "
    "read-only and plan how to parse it into Claude-controllable system capabilities. "
    "Return concise JSON only. Do not modify files."
)

PROMPT_AGENTIC_ANALYSIS_PLAN_USER = (
    "Kit name: {kit_name}\n\n"
    "Project root for cwd/reference only. Do not Read this directory path directly:\n{project_paths}\n\n"
    "High-signal files to inspect read-only. Read these exact files first; skip paths that do not exist:\n"
    "{plan_source_hints}\n\n"
    "Representative source excerpts from those files:\n"
    "{plan_source_context}\n\n"
    "Scanner hints:\n"
    "{capability_summary}\n\n"
    "No tool use is needed in this planning pass; use the scanner hints and source excerpts above. "
    "Do not attempt repository exploration, shell commands, sub-agents, or directory-wide analysis. "
    "Keep this as a quick planning pass. "
    "Return a JSON object with "
    "keys: project_summary, main_capability_names, remaining_strategy, questions, notes_for_generation. "
    "Focus on the project structure and the most important capabilities. Do not do exhaustive analysis."
)


def normalize_agent_analysis(parsed: dict[str, Any]) -> dict[str, Any]:
    analysis = parsed.get("project_analysis")
    if not isinstance(analysis, dict):
        analysis = {}
    return {
        "summary": analysis.get("summary", ""),
        "business_objects": analysis.get("business_objects", []),
        "workflows": analysis.get("workflows", []),
        "permission_boundaries": analysis.get("permission_boundaries", []),
        "side_effects": analysis.get("side_effects", []),
        "assumptions": analysis.get("assumptions", []),
        "tool_enhancements": parsed.get("tool_enhancements", {}),
        "risk_assessments": parsed.get("risk_assessments", {}),
        "additional_tools": parsed.get("additional_tools", []),
    }


def _run_async(coro: Any) -> Any:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    return asyncio.run(coro)


def _build_agentic_plan_inventory(capabilities: list[Capability], sample_limit: int = 24) -> dict[str, Any]:
    domain_counts = Counter(cap.domain for cap in capabilities)
    action_counts = Counter(cap.action for cap in capabilities)
    risk_counts = Counter(cap.risk for cap in capabilities)
    source_counts = Counter(cap.source.path for cap in capabilities if cap.source.path)
    samples = [
        {
            "name": cap.name,
            "domain": cap.domain,
            "resource": cap.resource,
            "action": cap.action,
            "risk": cap.risk,
            "source": {"kind": cap.source.kind, "location": cap.source.location},
        }
        for cap in _rank_capabilities_for_inventory(capabilities)[:sample_limit]
    ]
    return {
        "candidate_count": len(capabilities),
        "top_domains": dict(domain_counts.most_common(10)),
        "top_actions": dict(action_counts.most_common(10)),
        "risk_summary": dict(risk_counts.most_common()),
        "high_signal_paths": [path for path, _count in source_counts.most_common(12)],
        "sample_capabilities": samples,
        "sample_limit": sample_limit,
        "note": "This is a compact hint set. Use project files as the source of truth.",
    }


def _rank_capabilities_for_inventory(capabilities: list[Capability]) -> list[Capability]:
    source_priority = {"openapi": 5, "graphql": 4, "source_route": 3, "database_schema": 2, "warning": 0}
    action_priority = {"create": 5, "update": 4, "list": 3, "get": 2, "delete": 1}
    return sorted(
        capabilities,
        key=lambda cap: (
            -source_priority.get(cap.source.kind, 1),
            -action_priority.get(cap.action, 1),
            cap.domain,
            cap.resource,
            cap.name,
        ),
    )


def _format_agentic_plan_inventory(inventory: dict[str, Any]) -> str:
    def _items(mapping: Any, limit: int = 8) -> str:
        if not isinstance(mapping, dict):
            return "none"
        return ", ".join(f"{key}={value}" for key, value in list(mapping.items())[:limit]) or "none"

    lines = [
        f"- candidate_count: {inventory.get('candidate_count', 0)}",
        f"- top_domains: {_items(inventory.get('top_domains'))}",
        f"- top_actions: {_items(inventory.get('top_actions'))}",
        f"- risk_summary: {_items(inventory.get('risk_summary'))}",
        "- high_signal_paths:",
    ]
    for path in inventory.get("high_signal_paths", [])[:12]:
        lines.append(f"  - {path}")
    lines.append("- sample_capabilities:")
    for sample in inventory.get("sample_capabilities", [])[:12]:
        if not isinstance(sample, dict):
            continue
        source = sample.get("source", {}) if isinstance(sample.get("source"), dict) else {}
        location = source.get("location", "")
        lines.append(
            f"  - {sample.get('name')} ({sample.get('domain')}/{sample.get('action')}, "
            f"risk={sample.get('risk')}, source={source.get('kind', '')} {location})"
        )
    return "\n".join(lines)


def _format_plan_source_hints(input_paths: list[Path], inventory: dict[str, Any], limit: int = 14) -> str:
    roots = [path.resolve() for path in input_paths if path.is_dir()]
    candidates = [
        "CLAUDE.md",
        "README.md",
        "package.json",
        "services/api/openapi.json",
        "services/api/openapi-zh.json",
        "docs/api.md",
        "docs/specs-overview.md",
        "services/api/prisma/schema.prisma",
        "services/api/prisma/migrations/0_init/migration.sql",
    ]
    candidates.extend(str(path) for path in inventory.get("high_signal_paths", []) if path)

    seen: set[str] = set()
    lines: list[str] = []

    def _add(label: str) -> None:
        if label and label not in seen and len(lines) < limit:
            seen.add(label)
            lines.append(f"- {label}")

    for path in input_paths:
        if path.is_file():
            _add(str(path.resolve()))

    for candidate in candidates:
        candidate_path = Path(candidate)
        if candidate_path.is_absolute():
            if candidate_path.is_file():
                _add(str(candidate_path))
            continue
        for root in roots:
            resolved = root / candidate
            if resolved.is_file():
                _add(candidate)
                break

    return "\n".join(lines) or "- <none>"


def _format_batch_source_hints(
    capabilities: list[Capability],
    limit: int = 12,
    input_paths: list[Path] | None = None,
) -> str:
    roots = [path.resolve() for path in input_paths or [] if path.is_dir()]
    seen: set[tuple[str, str]] = set()
    lines: list[str] = []
    for cap in capabilities:
        key = (cap.source.path, cap.source.location)
        if key in seen:
            continue
        seen.add(key)
        detail = f"{cap.source.path}"
        if cap.source.location:
            detail += f" ({cap.source.location})"
        lines.append(f"- {detail}")
        for module_guess in _module_source_guesses(cap):
            if len(lines) >= limit:
                break
            if roots and not any((root / module_guess).is_file() for root in roots):
                continue
            guess_key = (module_guess, "")
            if guess_key in seen:
                continue
            seen.add(guess_key)
            lines.append(f"- {module_guess}")
        for adjacent in _adjacent_source_guesses(cap.source.path):
            if len(lines) >= limit:
                break
            guess_key = (adjacent, "")
            if guess_key in seen:
                continue
            seen.add(guess_key)
            lines.append(f"- {adjacent}")
        if len(lines) >= limit:
            break
    return "\n".join(lines[:limit]) or "- <none>"


def _module_source_guesses(capability: Capability) -> list[str]:
    names: list[str] = []
    normalized = _module_name_guess(capability.resource)
    if normalized:
        names.append(normalized)
    guesses: list[str] = []
    for name in names:
        guesses.extend(
            [
                f"services/api/src/modules/{name}/{name}.controller.ts",
                f"services/api/src/modules/{name}/{name}.service.ts",
                f"services/api/src/modules/{name}/{name}-v1.controller.ts",
                f"services/api/src/modules/{name}/{name}-v1.service.ts",
            ]
        )
    return guesses


def _module_name_guess(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if not normalized or normalized in {"inferred", "unknown"}:
        return ""
    if normalized.endswith("y") and len(normalized) > 1 and normalized[-2] not in "aeiou":
        return f"{normalized[:-1]}ies"
    if normalized.endswith(("s", "x", "z", "ch", "sh")):
        return f"{normalized}es" if not normalized.endswith("s") else normalized
    return f"{normalized}s"


def _adjacent_source_guesses(path: str) -> list[str]:
    if not path:
        return []
    source = Path(path)
    guesses: list[str] = []
    name = source.name
    if name.endswith(".controller.ts"):
        guesses.append(str(source.with_name(name.replace(".controller.ts", ".service.ts"))))
    elif name.endswith(".service.ts"):
        guesses.append(str(source.with_name(name.replace(".service.ts", ".controller.ts"))))
    if source.name in {"openapi.json", "openapi-zh.json"}:
        guesses.append(str(source.parent / "prisma" / "schema.prisma"))
    return guesses


def _invalid_generation_json_message(text: str) -> str:
    message = (
        "LLM failed to return valid JSON for generation. "
        "Please check your API key and model configuration."
    )
    if os.environ.get("AGENTBRIDGE_DEBUG_LLM"):
        preview = text.strip().replace("\n", "\\n")[:1000] or "<empty response>"
        return f"{message} Response preview: {preview}"
    return f"{message} Set AGENTBRIDGE_DEBUG_LLM=1 to include a response preview."


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name, "")
    if not value:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _bounded_agent_timeout(configured_timeout: float, llm_timeout: float) -> float:
    return min(configured_timeout, llm_timeout) if llm_timeout > 0 else configured_timeout


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "")
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _is_timeout_error(exc: Exception) -> bool:
    names = {exc.__class__.__name__}
    cause = exc.__cause__
    if cause is not None:
        names.add(cause.__class__.__name__)
    return bool(names & {"APITimeoutError", "ReadTimeout", "TimeoutException", "TimeoutError"})


def _claude_agent_sdk_available() -> bool:
    return _module_available("claude_agent_sdk")


def _agent_sdk_session_id(kit_dir: Path, session_id: str) -> str:
    try:
        return str(uuid.UUID(session_id))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"agentbridge:{kit_dir.resolve()}:{session_id}"))


def _anthropic_available() -> bool:
    return _module_available("anthropic")


def _module_available(name: str) -> bool:
    import sys

    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _project_cwd(input_paths: list[Path]) -> Path | None:
    for path in input_paths:
        if path.is_dir():
            return path.resolve()
    for path in input_paths:
        if path.exists():
            return (path if path.is_dir() else path.parent).resolve()
    return None


def _construct_with_supported_kwargs(factory: Any, kwargs: dict[str, Any]) -> Any:
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return factory(**kwargs)
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return factory(**kwargs)
    supported = {
        name
        for name, param in signature.parameters.items()
        if param.kind in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
    }
    return factory(**{key: value for key, value in kwargs.items() if key in supported})


def _extract_assistant_texts(message: Any) -> list[str]:
    if not _looks_like_assistant_message(message):
        return []
    texts: list[str] = []
    content = getattr(message, "content", None)
    for block in _iter_content_blocks(content):
        text = _block_text(block)
        if text is not None:
            texts.append(text)
    if not texts:
        text = getattr(message, "text", None)
        if isinstance(text, str):
            texts.append(text)
    return texts


def _agent_sdk_progress_events(message: Any) -> list[str]:
    events: list[str] = []
    message_type = _message_type(message)
    if message_type:
        if message_type in {"ResultMessage", "result"}:
            events.append(f"Claude Agent SDK event: {message_type}.")

    for block in _iter_content_blocks(getattr(message, "content", None)):
        block_type = _block_type(block)
        if block_type == "tool_use" or block_type.endswith("ToolUseBlock"):
            tool_name = str(getattr(block, "name", "") or getattr(block, "tool_name", "") or "tool")
            tool_input = getattr(block, "input", None)
            events.append(_format_tool_progress(tool_name, tool_input))
        elif block_type == "tool_result" or block_type.endswith("ToolResultBlock"):
            events.append(_format_tool_result_progress(block))
        elif "thinking" in block_type.lower() or "reasoning" in block_type.lower():
            events.append("Claude Agent SDK internal reasoning step completed; details hidden.")
        elif _block_text(block) is not None and _looks_like_assistant_message(message):
            text = str(_block_text(block) or "")
            preview = text.strip().replace("\n", " ")
            json_event = _json_progress_event(text)
            if json_event:
                events.append(json_event)
            elif preview and not preview.startswith("{"):
                events.append(f"Claude Agent SDK assistant update: {preview[:160]}")
            else:
                events.append(f"Claude Agent SDK assistant text received ({len(text)} chars).")
    if not events and message_type and message_type not in {"SystemMessage", "system"}:
        events.append(f"Claude Agent SDK event: {message_type}.")
    return events


def _looks_like_assistant_message(message: Any) -> bool:
    cls_name = message.__class__.__name__
    role = getattr(message, "role", None)
    return cls_name == "AssistantMessage" or role == "assistant"


def _message_type(message: Any) -> str:
    value = getattr(message, "type", None)
    if isinstance(value, str):
        return value
    return message.__class__.__name__


def _iter_content_blocks(content: Any) -> list[Any]:
    if content is None:
        return []
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return content
    if isinstance(content, tuple):
        return list(content)
    return [content]


def _block_type(block: Any) -> str:
    if isinstance(block, dict):
        return str(block.get("type", ""))
    value = getattr(block, "type", None)
    if isinstance(value, str):
        return value
    return block.__class__.__name__


def _block_text(block: Any) -> str | None:
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        text = block.get("text")
        return text if isinstance(text, str) else None
    text = getattr(block, "text", None)
    return text if isinstance(text, str) else None


def _tool_input_preview(tool_input: Any) -> str:
    if tool_input in (None, ""):
        return "."
    try:
        payload = json.dumps(tool_input, sort_keys=True, default=str)
    except TypeError:
        payload = str(tool_input)
    payload = payload.replace("\n", " ")
    return f" {payload[:240]}."


def _format_tool_progress(tool_name: str, tool_input: Any) -> str:
    lowered = tool_name.lower()
    if "read" in lowered:
        path = _tool_input_value(tool_input, "file_path", "path")
        if path:
            return f"Claude Agent SDK reading file: {path}"
    if "grep" in lowered or "search" in lowered:
        pattern = _tool_input_value(tool_input, "pattern", "query")
        path = _tool_input_value(tool_input, "path", "glob")
        if pattern and path:
            return f"Claude Agent SDK searching code: {pattern} in {path}"
        if pattern:
            return f"Claude Agent SDK searching code: {pattern}"
    if lowered in {"glob"} or "glob" in lowered:
        pattern = _tool_input_value(tool_input, "pattern", "glob")
        if pattern:
            return f"Claude Agent SDK finding files: {pattern}"
    if lowered in {"ls", "list"} or "ls" in lowered:
        path = _tool_input_value(tool_input, "path")
        if path:
            return f"Claude Agent SDK listing path: {path}"
    return f"Claude Agent SDK tool call: {tool_name}{_tool_input_preview(tool_input)}"


def _tool_input_value(tool_input: Any, *keys: str) -> str:
    if isinstance(tool_input, dict):
        for key in keys:
            value = tool_input.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def _format_tool_result_progress(block: Any) -> str:
    tool_use_id = getattr(block, "tool_use_id", "")
    suffix = f" for {tool_use_id}" if tool_use_id else ""
    summary = _block_summary(getattr(block, "content", None) or getattr(block, "result", None) or getattr(block, "text", None))
    if summary:
        return f"Claude Agent SDK tool result received{suffix}: {summary}"
    return f"Claude Agent SDK tool result received{suffix}."


def _block_summary(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip().replace("\n", " ")
        if not text:
            return ""
        return text[:160]
    if isinstance(value, dict):
        if "path" in value and value.get("path"):
            return f"path={value.get('path')}"
        if "content" in value and value.get("content"):
            return _block_summary(value.get("content"))
        try:
            payload = json.dumps(value, sort_keys=True, default=str)
        except TypeError:
            payload = str(value)
        return payload.replace("\n", " ")[:160]
    if isinstance(value, list):
        if not value:
            return "empty result"
        if len(value) == 1:
            return _block_summary(value[0])
        return f"{len(value)} item(s)"
    text = str(value).strip().replace("\n", " ")
    return text[:160]


def _json_progress_event(text: str) -> str:
    parsed = _parse_json_object(text, {})
    if not parsed:
        return ""
    if parsed.get("project_summary") or parsed.get("main_capability_names") or parsed.get("questions"):
        summary = str(parsed.get("project_summary", "") or "").strip()
        main_count = len(parsed.get("main_capability_names", []) or [])
        question_count = len(parsed.get("questions", []) or [])
        bits = []
        if summary:
            bits.append(f"summary: {summary[:160]}")
        if main_count:
            bits.append(f"{main_count} prioritized main capabilities")
        if question_count:
            bits.append(f"{question_count} question(s)")
        return f"Claude Agent SDK project plan received ({'; '.join(bits)})."
    if "project_analysis" in parsed or "tool_enhancements" in parsed:
        enhancements = parsed.get("tool_enhancements", {})
        count = len(enhancements) if isinstance(enhancements, dict) else 0
        return f"Claude Agent SDK generated batch analysis JSON ({count} tool enhancement(s))."
    return "Claude Agent SDK assistant JSON received."


def _parse_json_object(text: str, fallback: dict[str, Any]) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    expected_keys = {
        "project_analysis",
        "tool_enhancements",
        "risk_assessments",
        "additional_tools",
        "system_prompt",
        "skills",
        "project_summary",
        "main_capability_names",
        "remaining_strategy",
        "notes_for_generation",
    }
    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            score = sum(1 for key in expected_keys if key in parsed)
            candidates.append((score, start, parsed))
    if not candidates:
        return fallback
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[-1][2]


def _parse_json_array(text: str, fallback: list[Any]) -> list[Any]:
    decoder = json.JSONDecoder()
    for start, char in enumerate(text):
        if char != "[":
            continue
        try:
            parsed, _end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            return parsed
    return fallback
