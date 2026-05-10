from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from agentbridge.models import Capability

_DEFAULT_MODEL = "claude-sonnet-4-20250514"


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
                "LLM API key is required for AI generation. "
                "Set ANTHROPIC_API_KEY environment variable or pass api_key parameter."
            )
        self.base_url = base_url or os.environ.get("ANTHROPIC_BASE_URL", "")
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "") or _DEFAULT_MODEL

        os.environ.setdefault("ANTHROPIC_API_KEY", self.api_key)
        if self.base_url:
            os.environ.setdefault("ANTHROPIC_BASE_URL", self.base_url)

        self._backend = self._detect_backend()

    def _detect_backend(self) -> str:
        has_custom_endpoint = bool(self.base_url)
        if has_custom_endpoint:
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

    def enhance_tools(self, capabilities: list[Capability]) -> dict[str, dict[str, Any]]:
        return _run_async(self._enhance_tools_async(capabilities))

    def generate_skills(self, capabilities: list[Capability], kit_name: str) -> dict[str, str]:
        return _run_async(self._generate_skills_async(capabilities, kit_name))

    def generate_system_prompt(self, capabilities: list[Capability], kit_name: str) -> str:
        return _run_async(self._generate_system_prompt_async(capabilities, kit_name))

    def infer_additional_tools(self, capabilities: list[Capability]) -> list[dict[str, Any]]:
        return _run_async(self._infer_additional_tools_async(capabilities))

    def enhance_risk_assessment(self, capabilities: list[Capability]) -> dict[str, dict[str, Any]]:
        return _run_async(self._enhance_risk_assessment_async(capabilities))

    async def _ask_agent_sdk(self, system_prompt: str, user_prompt: str) -> str:
        from claude_agent_sdk import AssistantMessage, TextBlock
        from claude_agent_sdk import ClaudeAgentOptions
        from claude_agent_sdk import query

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            max_turns=1,
        )
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
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        for block in response.content:
            if block.type == "text":
                return block.text
        return ""

    async def _enhance_tools_async(self, capabilities: list[Capability]) -> dict[str, dict[str, Any]]:
        caps_data = [cap.to_dict() for cap in capabilities]
        result = await self._ask(
            PROMPT_TOOL_ENHANCEMENT_SYSTEM,
            PROMPT_TOOL_ENHANCEMENT_USER.format(capabilities=json.dumps(caps_data, indent=2)),
        )
        return _parse_json_object(result, {})

    async def _generate_skills_async(self, capabilities: list[Capability], kit_name: str) -> dict[str, str]:
        caps_data = [cap.to_dict() for cap in capabilities]
        domains = sorted({cap.domain for cap in capabilities})
        result = await self._ask(
            PROMPT_SKILL_SYSTEM,
            PROMPT_SKILL_USER.format(
                capabilities=json.dumps(caps_data, indent=2),
                kit_name=kit_name,
                domains=", ".join(domains),
            ),
        )
        return _parse_json_object(result, {d: "" for d in domains})

    async def _generate_system_prompt_async(self, capabilities: list[Capability], kit_name: str) -> str:
        caps_data = [cap.to_dict() for cap in capabilities]
        return await self._ask(
            PROMPT_SYSTEM_PROMPT_SYSTEM,
            PROMPT_SYSTEM_PROMPT_USER.format(
                capabilities=json.dumps(caps_data, indent=2),
                kit_name=kit_name,
            ),
        )

    async def _infer_additional_tools_async(self, capabilities: list[Capability]) -> list[dict[str, Any]]:
        caps_data = [cap.to_dict() for cap in capabilities]
        result = await self._ask(
            PROMPT_INFER_TOOLS_SYSTEM,
            PROMPT_INFER_TOOLS_USER.format(capabilities=json.dumps(caps_data, indent=2)),
        )
        return _parse_json_array(result, [])

    async def _enhance_risk_assessment_async(self, capabilities: list[Capability]) -> dict[str, dict[str, Any]]:
        caps_data = [cap.to_dict() for cap in capabilities]
        result = await self._ask(
            PROMPT_RISK_SYSTEM,
            PROMPT_RISK_USER.format(capabilities=json.dumps(caps_data, indent=2)),
        )
        return _parse_json_object(result, {})


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


PROMPT_TOOL_ENHANCEMENT_SYSTEM = (
    "You are an expert at designing AI agent tool interfaces. "
    "You receive discovered capabilities from an existing system and generate "
    "enhanced tool descriptions that capture business semantics. "
    "Always respond with valid JSON only, no markdown fences."
)

PROMPT_TOOL_ENHANCEMENT_USER = (
    "Given the following discovered capabilities, generate enhanced tool descriptions.\n\n"
    "For each tool, provide:\n"
    '1. "description": A clear, user-friendly description that explains what the tool does in business terms\n'
    '2. "when_to_use": When an AI agent should use this tool\n'
    '3. "caveats": Important caveats or edge cases the agent should know about\n\n'
    "Input capabilities:\n{capabilities}\n\n"
    "Respond with a JSON object where keys are tool names and values are objects with "
    '"description", "when_to_use", and "caveats" fields.'
)

PROMPT_SKILL_SYSTEM = (
    "You are an expert at designing AI agent skills for domain-specific workflows. "
    "You receive discovered capabilities and generate domain-specific skill documents in Markdown. "
    "Always respond with valid JSON only, no markdown fences."
)

PROMPT_SKILL_USER = (
    "Kit name: {kit_name}\n"
    "Domains: {domains}\n\n"
    "Input capabilities:\n{capabilities}\n\n"
    "For each domain, generate a skill document in Markdown that includes:\n"
    "1. When to activate this skill\n"
    "2. Step-by-step workflow for common operations\n"
    "3. Error handling and edge cases\n"
    "4. Best practices for this domain\n\n"
    'Respond with a JSON object where keys are domain names and values are the Markdown skill content (strings).'
)

PROMPT_SYSTEM_PROMPT_SYSTEM = (
    "You are an expert at designing AI agent system prompts. "
    "You receive discovered capabilities from an existing system and generate "
    "a comprehensive system prompt in Markdown. "
    "Respond with the system prompt as plain Markdown text."
)

PROMPT_SYSTEM_PROMPT_USER = (
    "Kit name: {kit_name}\n\n"
    "Input capabilities:\n{capabilities}\n\n"
    "Generate a system prompt that:\n"
    "1. Defines the agent's role and personality\n"
    "2. Explains the available capabilities in user-friendly terms\n"
    "3. Defines safety rules and operation procedures\n"
    "4. Guides the agent on when to ask for clarification vs. proceed\n"
    "5. Includes error handling guidance"
)

PROMPT_INFER_TOOLS_SYSTEM = (
    "You are an expert at analyzing API schemas and inferring additional tools "
    "that would be useful for an AI agent. "
    "Always respond with valid JSON only, no markdown fences."
)

PROMPT_INFER_TOOLS_USER = (
    "Given the following discovered capabilities, suggest additional tools that are "
    "implied by the schema but not explicitly present.\n\n"
    "Input capabilities:\n{capabilities}\n\n"
    "Consider:\n"
    "1. Search/filter operations that might be needed\n"
    "2. Batch operations for efficiency\n"
    "3. Validation/preview operations\n"
    "4. Status/check operations\n"
    "5. Relationship traversal tools\n\n"
    "For each suggested tool, provide:\n"
    '- "name": tool name in snake_case\n'
    '- "description": what it does\n'
    '- "input_schema": JSON Schema for parameters\n'
    '- "risk": one of "read", "write", "destructive", "external_side_effect"\n'
    '- "domain": logical domain grouping\n'
    '- "resource": target resource\n'
    '- "action": action verb\n'
    '- "rationale": why this tool is needed\n\n'
    "Respond with a JSON array of suggested tools."
)

PROMPT_RISK_SYSTEM = (
    "You are an expert at assessing the risk level of API operations. "
    "You receive discovered capabilities and provide enhanced risk assessments "
    "that go beyond simple keyword matching. "
    "Always respond with valid JSON only, no markdown fences."
)

PROMPT_RISK_USER = (
    "Given the following discovered capabilities, provide enhanced risk assessments.\n\n"
    "Input capabilities:\n{capabilities}\n\n"
    "For each tool, consider:\n"
    "1. The actual business impact of the operation\n"
    "2. Whether the operation is reversible\n"
    "3. Whether it affects multiple resources\n"
    "4. Whether it has external side effects\n"
    "5. Whether it involves sensitive data\n\n"
    "Respond with a JSON object where keys are tool names and values are objects with:\n"
    '- "risk": one of "read", "write", "destructive", "external_side_effect"\n'
    '- "reason": detailed reasoning for the risk level\n'
    '- "reversible": whether the operation can be undone (boolean)\n'
    '- "blast_radius": "single" or "multiple" resources affected'
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
