from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

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
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "LLM API key is required. "
                "Set ANTHROPIC_API_KEY environment variable or pass api_key parameter."
            )
        self.base_url = base_url or os.environ.get("ANTHROPIC_BASE_URL", "")
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "") or _DEFAULT_MODEL

        os.environ.setdefault("ANTHROPIC_API_KEY", self.api_key)
        if self.base_url:
            os.environ.setdefault("ANTHROPIC_BASE_URL", self.base_url)

        self._backend = self._detect_backend()

    def _detect_backend(self) -> str:
        if self.base_url:
            return self._BACKEND_ANTHROPIC
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
        rule_context = self._build_rule_context(capabilities)
        source_context = self._build_source_context(input_paths or [])
        return _run_async(
            self._generate_all_async(capabilities, kit_name, rule_context, source_context, input_paths)
        )

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
                cwd_hint = f"\n\nThe project directory is: {first}\nYou can use the Read tool to explore additional files if needed."

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

        parsed = _parse_json_object(result, {})
        if not parsed:
            raise RuntimeError("LLM failed to return valid JSON for generation. Please check your API key and model configuration.")

        enhanced_caps = self._apply_enhanced_capabilities(capabilities, parsed)
        system_prompt = parsed.get("system_prompt", "")
        skills = parsed.get("skills", {})

        return {
            "enhanced_capabilities": enhanced_caps,
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
        from claude_agent_sdk import AssistantMessage, TextBlock
        from claude_agent_sdk import ClaudeAgentOptions
        from claude_agent_sdk import query

        options_kwargs: dict[str, Any] = {
            "system_prompt": system_prompt,
            "max_turns": 3,
        }
        if cwd:
            options_kwargs["cwd"] = cwd
        options = ClaudeAgentOptions(**options_kwargs)
        collected: list[str] = []
        async for message in query(prompt=user_prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        collected.append(block.text)
        return "".join(collected)

    async def _ask(self, system_prompt: str, user_prompt: str) -> str:
        if self._backend == self._BACKEND_AGENT_SDK:
            return await self._ask_agent_sdk(system_prompt, user_prompt)
        return await asyncio.to_thread(self._ask_anthropic_sync, system_prompt, user_prompt)

    def _ask_anthropic_sync(self, system_prompt: str, user_prompt: str) -> str:
        import anthropic

        kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        client = anthropic.Anthropic(**kwargs)
        response = client.messages.create(
            model=self.model,
            max_tokens=8192,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        for block in response.content:
            if block.type == "text":
                return block.text
        return ""


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
    "You are an expert at designing AI agent integration kits for existing systems. "
    "You receive the PROJECT SOURCE CODE, discovered capabilities from rule-based analysis, "
    "and rule-based risk assessments as context. "
    "Your job is to generate a COMPLETE agent integration kit in a single response. "
    "You must read and understand the source code to generate accurate, domain-specific content. "
    "Use the rule-based context as a starting point but apply your own judgment based on "
    "what you learn from reading the actual code — you may override rule-based risk levels "
    "when you have good reason. "
    "Always respond with valid JSON only, no markdown fences."
)

PROMPT_GENERATE_ALL_USER = (
    "Kit name: {kit_name}\n"
    "Domains: {domains}\n\n"
    "Discovered capabilities (from rule-based analysis):\n{capabilities}\n\n"
    "Rule-based analysis context (use as reference, not absolute truth):\n{rule_context}\n\n"
    "{source_section}"
    "{cwd_hint}\n\n"
    "Generate a complete agent integration kit. Respond with a JSON object containing:\n\n"
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


def _parse_json_object(text: str, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        start = text.index("{")
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : i + 1])
    except (ValueError, json.JSONDecodeError):
        pass
    return fallback


def _parse_json_array(text: str, fallback: list[Any]) -> list[Any]:
    try:
        start = text.index("[")
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : i + 1])
    except (ValueError, json.JSONDecodeError):
        pass
    return fallback
