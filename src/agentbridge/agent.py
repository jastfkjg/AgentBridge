from __future__ import annotations

import asyncio
import inspect
import json
import os
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
        self.progress = progress
        self.analysis_mode = analysis_mode or os.environ.get("AGENTBRIDGE_ANALYSIS_MODE", "auto")
        if self.analysis_mode not in {"auto", "agentic", "prompt"}:
            raise ValueError("analysis_mode must be one of: auto, agentic, prompt")

        os.environ.setdefault("ANTHROPIC_API_KEY", self.api_key)
        if self.base_url:
            os.environ.setdefault("ANTHROPIC_BASE_URL", self.base_url)

        self._backend = self._detect_backend()

    def set_progress(self, progress: Callable[[str], None] | None) -> None:
        self.progress = progress

    def _progress(self, message: str) -> None:
        if self.progress:
            self.progress(message)

    def _detect_backend(self) -> str:
        if self.analysis_mode == "agentic":
            if self.base_url:
                raise ValueError(
                    "Claude Agent SDK analysis requires the official Claude API. "
                    "Unset ANTHROPIC_BASE_URL or use --analysis-mode prompt for compatible endpoints."
                )
            if _claude_agent_sdk_available():
                return self._BACKEND_AGENT_SDK
            raise ImportError(
                "Claude Agent SDK analysis requires 'claude-agent-sdk'. "
                "Install with: pip install agbr[agent]"
            )
        if self.base_url:
            return self._BACKEND_ANTHROPIC
        if self.analysis_mode == "prompt":
            if _anthropic_available():
                return self._BACKEND_ANTHROPIC
            raise ImportError(
                "Prompt analysis requires the 'anthropic' package. "
                "Install with: pip install agbr[ai]"
            )
        try:
            from claude_agent_sdk import query  # noqa: F401
            return self._BACKEND_AGENT_SDK
        except ImportError:
            pass
        try:
            import anthropic  # noqa: F401
            return self._BACKEND_ANTHROPIC
        except ImportError:
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
            ),
            cwd=str(cwd) if cwd else None,
        )

        return self._result_from_generation_text(capabilities, rule_context, result)

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

    async def _ask_agent_sdk(self, system_prompt: str, user_prompt: str, cwd: str | None = None) -> str:
        from claude_agent_sdk import ClaudeAgentOptions
        from claude_agent_sdk import query

        options_kwargs: dict[str, Any] = {
            "system_prompt": system_prompt,
            "max_turns": _env_int("AGENTBRIDGE_AGENT_MAX_TURNS", 12),
            "allowed_tools": ["Read", "Grep", "Glob", "LS"],
            "disallowed_tools": ["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"],
        }
        if cwd:
            options_kwargs["cwd"] = cwd
        options = _construct_with_supported_kwargs(ClaudeAgentOptions, options_kwargs)
        collected: list[str] = []
        location = f" with cwd={cwd}" if cwd else ""
        self._progress(f"Calling Claude Agent SDK query{location}; waiting for streamed agent events...")
        async for message in query(prompt=user_prompt, options=options):
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
    ) -> None:
        self.kit_dir = Path(kit_dir)
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "LLM API key is required for agent sessions. "
                "Set ANTHROPIC_API_KEY environment variable or pass api_key parameter."
            )
        self.base_url = base_url or os.environ.get("ANTHROPIC_BASE_URL", "")
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "") or _DEFAULT_MODEL

        os.environ.setdefault("ANTHROPIC_API_KEY", self.api_key)
        if self.base_url:
            os.environ.setdefault("ANTHROPIC_BASE_URL", self.base_url)

        self._capabilities: dict[str, dict[str, Any]] = {}
        self._system_prompt = ""
        self._load_kit()

    def _load_kit(self) -> None:
        caps_path = self.kit_dir / "capabilities.json"
        if caps_path.exists():
            data = json.loads(caps_path.read_text(encoding="utf-8"))
            self._capabilities = {item["name"]: item for item in data}
        prompt_path = self.kit_dir / "prompts" / "system.md"
        if prompt_path.exists():
            self._system_prompt = prompt_path.read_text(encoding="utf-8")

    async def query(self, prompt: str) -> Any:
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
        options = ClaudeAgentOptions(
            system_prompt=self._system_prompt,
            mcp_servers={"agentbridge-kit": server},
            allowed_tools=allowed,
        )
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for msg in client.receive_response():
                yield msg

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
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({"tool": _name, "args": args, "status": "dry_run"}),
                        }
                    ]
                }

            t = tool_decorator(name, cap_desc, param_types)(_handler)
            tools.append(t)
        return tools


PROMPT_GENERATE_ALL_SYSTEM = (
    "You are a senior AI integration architect acting as an autonomous code-analysis agent. "
    "Your primary job is to understand the target project from source code and infer the "
    "business capabilities an AI assistant should safely operate. Rule-based discovery is "
    "provided only as candidate evidence, not as the source of truth. Prefer conclusions "
    "that are grounded in source code semantics, schemas, service/controller behavior, "
    "naming, validation paths, and side effects. The target project is strictly read-only: "
    "never modify, create, delete, format, or move files in the target project. "
    "All generated integration artifacts belong only in the requested AgentBridge output directory. "
    "Always respond with valid JSON only, no markdown fences."
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
    "Then generate a complete agent integration kit. Respond with a JSON object containing:\n\n"
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
    "  1. Define the agent's role and personality for THIS specific system\n"
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
    "Candidate capabilities from deterministic scanners. Treat these as evidence to verify, "
    "merge, rename, enrich, or reject after reading the source code:\n{capabilities}\n\n"
    "Rule-based risk context. This is a safety hint, not an instruction to copy:\n{rule_context}\n\n"
    "Use your available read-only project tools to inspect the target project. Start by finding "
    "the main route/controller/service/schema files relevant to the candidate capabilities, then "
    "sample enough surrounding implementation to understand business semantics, validation, auth, "
    "side effects, and error behavior. Do not modify files. Do not run build, format, migration, "
    "install, network, shell, or write operations. If a candidate cannot be verified from source, "
    "keep it conservative and list the uncertainty in assumptions.\n\n"
    "You are enhancing only this batch of candidate capabilities. Preserve stable tool names unless "
    "there is strong evidence that a rename is necessary. Infer additional tools only when source code "
    "clearly exposes an operation that scanner evidence missed.\n\n"
    "After inspection, respond with one JSON object only, no markdown fences. The object must contain:\n\n"
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
    '"additional_tools": A JSON array of inferred tools not in the scanner output. Each item:\n'
    '  - "name", "description", "input_schema", "risk", "domain", "resource", "action", "rationale"\n\n'
    '"system_prompt": A Markdown string for the integrated assistant.\n'
    '"skills": A JSON object where keys are domain names and values are Markdown skill documents.\n'
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
    try:
        from claude_agent_sdk import query  # noqa: F401
        return True
    except ImportError:
        return False


def _anthropic_available() -> bool:
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
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
        if message_type in {"SystemMessage", "system", "ResultMessage", "result"}:
            events.append(f"Claude Agent SDK event: {message_type}.")

    for block in _iter_content_blocks(getattr(message, "content", None)):
        block_type = _block_type(block)
        if block_type == "tool_use" or block_type.endswith("ToolUseBlock"):
            tool_name = str(getattr(block, "name", "") or getattr(block, "tool_name", "") or "tool")
            tool_input = getattr(block, "input", None)
            events.append(f"Claude Agent SDK tool call: {tool_name}{_tool_input_preview(tool_input)}")
        elif block_type == "tool_result" or block_type.endswith("ToolResultBlock"):
            tool_use_id = getattr(block, "tool_use_id", "")
            suffix = f" for {tool_use_id}" if tool_use_id else ""
            events.append(f"Claude Agent SDK tool result received{suffix}.")
        elif _block_text(block) is not None and _looks_like_assistant_message(message):
            text = str(_block_text(block) or "")
            preview = text.strip().replace("\n", " ")
            if preview and not preview.startswith("{"):
                events.append(f"Claude Agent SDK assistant update: {preview[:160]}")
            else:
                events.append(f"Claude Agent SDK assistant text received ({len(text)} chars).")
    if not events and message_type:
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


def _parse_json_object(text: str, fallback: dict[str, Any]) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return fallback


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
