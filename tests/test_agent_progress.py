import json
import asyncio
import builtins
import os
import sys
import tempfile
import threading
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from agentbridge.agent import AIGenerator, _extract_agent_result_text, _extract_agent_usage, _parse_json_object


class AgentProgressTests(unittest.TestCase):
    def test_extract_agent_result_text_reads_result_message_result(self):
        class FakeResultMessage:
            result = "你好，我可以帮你操作这个系统。"
            content = []

        text = _extract_agent_result_text(FakeResultMessage())

        self.assertEqual(text, "你好，我可以帮你操作这个系统。")

    def test_agent_runner_query_text_prefers_final_result_over_stream_text(self):
        class FakeAssistantMessage:
            content = [{"type": "text", "text": "draft response"}]
            result = None

        class FakeResultMessage:
            content = []
            result = "final response"
            usage = {"input_tokens": 120, "output_tokens": 30}

        class FakeRunner:
            async def query(self, _prompt):
                yield FakeAssistantMessage()
                yield FakeResultMessage()

        from agentbridge.agent import AgentRunner

        runner = AgentRunner.__new__(AgentRunner)
        runner.query = FakeRunner().query  # type: ignore[method-assign]

        self.assertEqual(runner.query_text("hello"), "final response")
        self.assertEqual(runner.last_usage["input_tokens"], 120)
        self.assertEqual(runner.last_usage["output_tokens"], 30)
        self.assertEqual(runner.last_usage["total_tokens"], 150)

    def test_agent_runner_reuses_one_sdk_client_for_session_queries(self):
        class FakeResultMessage:
            content = []
            usage = {"input_tokens": 10, "output_tokens": 5}

            def __init__(self, result: str) -> None:
                self.result = result

        class FakeClaudeAgentOptions:
            last_kwargs: dict[str, object] | None = None

            def __init__(self, **kwargs: object) -> None:
                FakeClaudeAgentOptions.last_kwargs = kwargs

        class FakeClaudeSDKClient:
            instances: list["FakeClaudeSDKClient"] = []

            def __init__(self, options: object) -> None:
                self.options = options
                self.connected = 0
                self.disconnected = 0
                self.query_calls: list[tuple[str, str]] = []
                FakeClaudeSDKClient.instances.append(self)

            async def connect(self) -> None:
                self.connected += 1

            async def disconnect(self) -> None:
                self.disconnected += 1

            async def query(self, prompt: str, session_id: str = "__not_passed__") -> None:
                self.query_calls.append((prompt, session_id))

            async def receive_response(self):
                yield FakeResultMessage(f"response {len(self.query_calls)}")

        def fake_tool(_name: str, _description: str, _param_types: dict[str, type]):
            def decorate(handler):
                return handler

            return decorate

        fake_module = types.ModuleType("claude_agent_sdk")
        fake_module.ClaudeAgentOptions = FakeClaudeAgentOptions
        fake_module.ClaudeSDKClient = FakeClaudeSDKClient
        fake_module.create_sdk_mcp_server = lambda **kwargs: kwargs
        fake_module.tool = fake_tool

        from agentbridge.agent import AgentRunner, _agent_sdk_session_id

        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {"claude_agent_sdk": fake_module}):
            kit = Path(tmp) / "kit"
            kit.mkdir()
            (kit / "capabilities.json").write_text(
                json.dumps(
                    [
                        {
                            "name": "list_chapter",
                            "domain": "writing",
                            "resource": "chapter",
                            "action": "list",
                            "description": "List chapters",
                            "input_schema": {"type": "object", "properties": {}},
                            "risk": "read",
                            "confirm_required": False,
                            "source": {"kind": "openapi", "path": "openapi.json", "location": "GET /chapters"},
                            "transport": {"type": "http", "method": "GET", "path": "/chapters"},
                            "dry_run_supported": True,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (kit / "guardrails").mkdir()
            (kit / "guardrails" / "permissions.json").write_text(
                json.dumps(
                    {
                        "tools": {
                            "list_chapter": {
                                "risk": "read",
                                "confirm_required": False,
                                "transport": {"type": "http", "method": "GET", "path": "/chapters"},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            runner = AgentRunner(kit, api_key="sk-test", session_id="web-session")
            sdk_session_id = _agent_sdk_session_id(kit, "web-session")

            self.assertEqual(runner.query_text("first"), "response 1")
            self.assertEqual(runner.query_text("second"), "response 2")
            runner.close()

        self.assertEqual(len(FakeClaudeSDKClient.instances), 1)
        client = FakeClaudeSDKClient.instances[0]
        self.assertEqual(client.connected, 1)
        self.assertEqual(client.disconnected, 1)
        self.assertEqual(client.query_calls, [("first", "__not_passed__"), ("second", "__not_passed__")])
        actual_session_id = str(FakeClaudeAgentOptions.last_kwargs["session_id"])
        self.assertNotEqual(actual_session_id, sdk_session_id)
        uuid.UUID(actual_session_id)

    def test_agent_runner_system_prompt_instructs_auth_refresh_on_expired_tokens(self):
        class FakeClaudeAgentOptions:
            last_kwargs: dict[str, object] | None = None

            def __init__(self, **kwargs: object) -> None:
                FakeClaudeAgentOptions.last_kwargs = kwargs

        class FakeClaudeSDKClient:
            def __init__(self, options: object) -> None:
                self.options = options

            async def connect(self) -> None:
                pass

            async def disconnect(self) -> None:
                pass

        def fake_tool(_name: str, _description: str, _param_types: dict[str, type]):
            def decorate(handler):
                return handler

            return decorate

        fake_module = types.ModuleType("claude_agent_sdk")
        fake_module.ClaudeAgentOptions = FakeClaudeAgentOptions
        fake_module.ClaudeSDKClient = FakeClaudeSDKClient
        fake_module.create_sdk_mcp_server = lambda **kwargs: kwargs
        fake_module.tool = fake_tool

        from agentbridge.agent import AgentRunner

        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {"claude_agent_sdk": fake_module}):
            kit = Path(tmp) / "kit"
            (kit / "prompts").mkdir(parents=True)
            (kit / "prompts" / "system.md").write_text("Operate this system.", encoding="utf-8")
            (kit / "capabilities.json").write_text(
                json.dumps(
                    [
                        {
                            "name": "login",
                            "domain": "auth",
                            "resource": "session",
                            "action": "create",
                            "description": "Login",
                            "input_schema": {"type": "object", "properties": {}, "required": []},
                            "risk": "external_side_effect",
                            "confirm_required": False,
                            "source": {"kind": "openapi", "path": "openapi.json", "location": "POST /auth/login"},
                            "transport": {"type": "http", "method": "POST", "path": "/auth/login"},
                            "dry_run_supported": True,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (kit / "guardrails").mkdir()
            (kit / "guardrails" / "permissions.json").write_text(json.dumps({"tools": {}}), encoding="utf-8")
            runner = AgentRunner(kit, api_key="sk-test", session_id="web-session")
            loop = runner._ensure_client_loop()
            future = asyncio.run_coroutine_threadsafe(runner._ensure_client_async(), loop)
            future.result(timeout=5)
            runner.close()

        system_prompt = str(FakeClaudeAgentOptions.last_kwargs["system_prompt"])
        self.assertIn("Token expired", system_prompt)
        self.assertIn("login", system_prompt)
        self.assertIn("Do not keep retrying the same expired token", system_prompt)

    def test_agent_runner_stream_messages_yields_before_response_completes(self):
        release_second_message = threading.Event()

        class FakeResultMessage:
            content = []
            usage = {"input_tokens": 1, "output_tokens": 1}

            def __init__(self, result: str) -> None:
                self.result = result

        class FakeClaudeAgentOptions:
            last_kwargs: dict[str, object] | None = None

            def __init__(self, **kwargs: object) -> None:
                FakeClaudeAgentOptions.last_kwargs = kwargs

        class FakeClaudeSDKClient:
            def __init__(self, options: object) -> None:
                self.options = options
                self.query_calls: list[str] = []

            async def connect(self) -> None:
                pass

            async def disconnect(self) -> None:
                pass

            async def query(self, prompt: str, session_id: str = "__not_passed__") -> None:
                self.query_calls.append(prompt)

            async def receive_response(self):
                yield FakeResultMessage("first")
                await asyncio.to_thread(release_second_message.wait)
                yield FakeResultMessage("second")

        def fake_tool(_name: str, _description: str, _param_types: dict[str, type]):
            def decorate(handler):
                return handler

            return decorate

        fake_module = types.ModuleType("claude_agent_sdk")
        fake_module.ClaudeAgentOptions = FakeClaudeAgentOptions
        fake_module.ClaudeSDKClient = FakeClaudeSDKClient
        fake_module.create_sdk_mcp_server = lambda **kwargs: kwargs
        fake_module.tool = fake_tool

        from agentbridge.agent import AgentRunner

        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {"claude_agent_sdk": fake_module}):
            kit = Path(tmp) / "kit"
            kit.mkdir()
            (kit / "capabilities.json").write_text("[]", encoding="utf-8")
            (kit / "guardrails").mkdir()
            (kit / "guardrails" / "permissions.json").write_text(json.dumps({"tools": {}}), encoding="utf-8")

            runner = AgentRunner(kit, api_key="sk-test", session_id="web-session")
            stream = runner.stream_messages("hello")
            first = next(stream)
            release_second_message.set()
            rest = list(stream)
            runner.close()

        self.assertEqual(first.result, "first")
        self.assertEqual([message.result for message in rest], ["second"])
        self.assertIs(FakeClaudeAgentOptions.last_kwargs["include_partial_messages"], True)

    def test_agent_runner_streams_sdk_permission_request_and_waits_for_decision(self):
        callback_result: dict[str, object] = {}

        class FakeResultMessage:
            content = []

            def __init__(self, result: str) -> None:
                self.result = result

        class FakePermissionContext:
            title = "Run command?"
            display_name = "Bash"
            description = "curl http://localhost:3001/api/v1/auth/login"
            tool_use_id = "toolu_1"

        class FakePermissionResultAllow:
            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs

        class FakePermissionResultDeny:
            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs

        class FakeClaudeAgentOptions:
            last_kwargs: dict[str, object] | None = None

            def __init__(self, **kwargs: object) -> None:
                FakeClaudeAgentOptions.last_kwargs = kwargs

        class FakeClaudeSDKClient:
            def __init__(self, options: object) -> None:
                self.options = options

            async def connect(self) -> None:
                pass

            async def disconnect(self) -> None:
                pass

            async def query(self, _prompt: str, session_id: str = "__not_passed__") -> None:
                pass

            async def receive_response(self):
                can_use_tool = FakeClaudeAgentOptions.last_kwargs["can_use_tool"]
                result = await can_use_tool("Bash", {"command": "curl -s http://localhost:3001/api/v1/auth/login"}, FakePermissionContext())
                callback_result["result"] = result
                yield FakeResultMessage("allowed")

        def fake_tool(_name: str, _description: str, _param_types: dict[str, type]):
            def decorate(handler):
                return handler

            return decorate

        fake_module = types.ModuleType("claude_agent_sdk")
        fake_module.ClaudeAgentOptions = FakeClaudeAgentOptions
        fake_module.ClaudeSDKClient = FakeClaudeSDKClient
        fake_module.PermissionResultAllow = FakePermissionResultAllow
        fake_module.PermissionResultDeny = FakePermissionResultDeny
        fake_module.create_sdk_mcp_server = lambda **kwargs: kwargs
        fake_module.tool = fake_tool

        from agentbridge.agent import AgentRunner

        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {"claude_agent_sdk": fake_module}):
            kit = Path(tmp) / "kit"
            kit.mkdir()
            (kit / "capabilities.json").write_text("[]", encoding="utf-8")
            (kit / "guardrails").mkdir()
            (kit / "guardrails" / "permissions.json").write_text(json.dumps({"tools": {}}), encoding="utf-8")

            runner = AgentRunner(kit, api_key="sk-test", session_id="web-session")
            stream = runner.stream_messages("run login")
            permission = next(stream)
            self.assertEqual(permission["type"], "agent_permission_required")
            self.assertEqual(permission["pending"]["tool"], "Bash")
            self.assertEqual(permission["pending"]["operation"], "Login")
            self.assertIn("curl", permission["pending"]["input"]["command"])

            self.assertTrue(runner.resolve_permission(permission["pending"]["id"], allow=True))
            rest = list(stream)
            runner.close()

        self.assertEqual(rest[0].result, "allowed")
        self.assertIsInstance(callback_result["result"], FakePermissionResultAllow)

    def test_agent_runner_auto_allows_read_only_bash_permission_requests(self):
        callback_result: dict[str, object] = {}

        class FakeResultMessage:
            content = []

            def __init__(self, result: str) -> None:
                self.result = result

        class FakePermissionContext:
            title = "Get script detail"
            display_name = "Bash"
            description = "curl -s http://localhost:3001/api/v1/scripts/1"
            tool_use_id = "toolu_1"

        class FakePermissionResultAllow:
            pass

        class FakePermissionResultDeny:
            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs

        class FakeClaudeAgentOptions:
            last_kwargs: dict[str, object] | None = None

            def __init__(self, **kwargs: object) -> None:
                FakeClaudeAgentOptions.last_kwargs = kwargs

        class FakeClaudeSDKClient:
            def __init__(self, options: object) -> None:
                self.options = options

            async def connect(self) -> None:
                pass

            async def disconnect(self) -> None:
                pass

            async def query(self, _prompt: str, session_id: str = "__not_passed__") -> None:
                pass

            async def receive_response(self):
                can_use_tool = FakeClaudeAgentOptions.last_kwargs["can_use_tool"]
                result = await can_use_tool("Bash", {"command": "TOKEN=abc curl -s http://localhost:3001/api/v1/scripts/1 -H 'Authorization: Bearer $TOKEN' | python3 -m json.tool"}, FakePermissionContext())
                callback_result["result"] = result
                yield FakeResultMessage("allowed")

        def fake_tool(_name: str, _description: str, _param_types: dict[str, type]):
            def decorate(handler):
                return handler

            return decorate

        fake_module = types.ModuleType("claude_agent_sdk")
        fake_module.ClaudeAgentOptions = FakeClaudeAgentOptions
        fake_module.ClaudeSDKClient = FakeClaudeSDKClient
        fake_module.PermissionResultAllow = FakePermissionResultAllow
        fake_module.PermissionResultDeny = FakePermissionResultDeny
        fake_module.create_sdk_mcp_server = lambda **kwargs: kwargs
        fake_module.tool = fake_tool

        from agentbridge.agent import AgentRunner

        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {"claude_agent_sdk": fake_module}):
            kit = Path(tmp) / "kit"
            kit.mkdir()
            (kit / "capabilities.json").write_text("[]", encoding="utf-8")
            (kit / "guardrails").mkdir()
            (kit / "guardrails" / "permissions.json").write_text(json.dumps({"tools": {}}), encoding="utf-8")

            runner = AgentRunner(kit, api_key="sk-test", session_id="web-session")
            messages = list(runner.stream_messages("get script detail"))
            runner.close()

        self.assertEqual([message.result for message in messages], ["allowed"])
        self.assertIsInstance(callback_result["result"], FakePermissionResultAllow)

    def test_agent_runner_uses_fresh_sdk_session_for_each_runner(self):
        from agentbridge.agent import AgentRunner, _agent_sdk_session_id

        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp) / "kit"
            kit.mkdir()
            (kit / "capabilities.json").write_text("[]", encoding="utf-8")
            (kit / "guardrails").mkdir()
            (kit / "guardrails" / "permissions.json").write_text(json.dumps({"tools": {}}), encoding="utf-8")
            stable_session_id = _agent_sdk_session_id(kit, "web-session")

            first = AgentRunner(kit, api_key="sk-test", session_id="web-session")
            second = AgentRunner(kit, api_key="sk-test", session_id="web-session")

        self.assertEqual(first._stable_sdk_session_id, stable_session_id)
        self.assertEqual(second._stable_sdk_session_id, stable_session_id)
        self.assertNotEqual(first.sdk_session_id, stable_session_id)
        self.assertNotEqual(second.sdk_session_id, stable_session_id)
        self.assertNotEqual(first.sdk_session_id, second.sdk_session_id)
        uuid.UUID(first.sdk_session_id)
        uuid.UUID(second.sdk_session_id)

    def test_agent_runner_retries_with_temporary_session_when_sdk_session_is_busy(self):
        class FakeResultMessage:
            content = []
            usage = {"input_tokens": 1, "output_tokens": 1}
            result = "fallback response"

        class FakeClaudeAgentOptions:
            instances: list["FakeClaudeAgentOptions"] = []

            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs
                FakeClaudeAgentOptions.instances.append(self)

        class FakeClaudeSDKClient:
            instances: list["FakeClaudeSDKClient"] = []

            def __init__(self, options: FakeClaudeAgentOptions) -> None:
                self.options = options
                self.disconnected = 0
                FakeClaudeSDKClient.instances.append(self)

            async def connect(self) -> None:
                session_id = str(self.options.kwargs.get("session_id"))
                if len(FakeClaudeSDKClient.instances) == 1:
                    raise RuntimeError(f"Error: Session ID {session_id} is already in use.")

            async def disconnect(self) -> None:
                self.disconnected += 1

            async def query(self, prompt: str, session_id: str = "__not_passed__") -> None:
                self.prompt = prompt
                self.query_session_id = session_id

            async def receive_response(self):
                yield FakeResultMessage()

        def fake_tool(_name: str, _description: str, _param_types: dict[str, type]):
            def decorate(handler):
                return handler

            return decorate

        fake_module = types.ModuleType("claude_agent_sdk")
        fake_module.ClaudeAgentOptions = FakeClaudeAgentOptions
        fake_module.ClaudeSDKClient = FakeClaudeSDKClient
        fake_module.create_sdk_mcp_server = lambda **kwargs: kwargs
        fake_module.tool = fake_tool

        from agentbridge.agent import AgentRunner, _agent_sdk_session_id

        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {"claude_agent_sdk": fake_module}):
            kit = Path(tmp) / "kit"
            kit.mkdir()
            (kit / "capabilities.json").write_text("[]", encoding="utf-8")
            (kit / "guardrails").mkdir()
            (kit / "guardrails" / "permissions.json").write_text(json.dumps({"tools": {}}), encoding="utf-8")
            stable_session_id = _agent_sdk_session_id(kit, "web-session")

            runner = AgentRunner(kit, api_key="sk-test", session_id="web-session")
            response = runner.query_text("hello")
            runner.close()

        self.assertEqual(response, "fallback response")
        self.assertEqual(len(FakeClaudeSDKClient.instances), 2)
        self.assertEqual(FakeClaudeSDKClient.instances[0].disconnected, 1)
        attempted_session_ids = [str(options.kwargs.get("session_id")) for options in FakeClaudeAgentOptions.instances]
        self.assertNotEqual(attempted_session_ids[0], stable_session_id)
        self.assertNotEqual(attempted_session_ids[1], stable_session_id)
        self.assertNotEqual(attempted_session_ids[1], attempted_session_ids[0])
        uuid.UUID(attempted_session_ids[0])
        uuid.UUID(attempted_session_ids[1])
        self.assertEqual(getattr(FakeClaudeSDKClient.instances[1], "query_session_id"), "__not_passed__")

    def test_agent_runner_retries_with_temporary_session_when_reader_reports_busy_session(self):
        class FakeResultMessage:
            content = []
            usage = {"input_tokens": 1, "output_tokens": 1}
            result = "fallback response"

        class FakeClaudeAgentOptions:
            instances: list["FakeClaudeAgentOptions"] = []

            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs
                FakeClaudeAgentOptions.instances.append(self)

        class FakeClaudeSDKClient:
            instances: list["FakeClaudeSDKClient"] = []

            def __init__(self, options: FakeClaudeAgentOptions) -> None:
                self.options = options
                self.disconnected = 0
                FakeClaudeSDKClient.instances.append(self)

            async def connect(self) -> None:
                pass

            async def disconnect(self) -> None:
                self.disconnected += 1

            async def query(self, prompt: str, session_id: str = "__not_passed__") -> None:
                self.prompt = prompt
                self.query_session_id = session_id

            async def receive_response(self):
                session_id = str(self.options.kwargs.get("session_id"))
                if len(FakeClaudeSDKClient.instances) == 1:
                    raise RuntimeError(
                        "Fatal error in message reader: Command failed with exit code 1 (exit code: 1)\n"
                        "Error output: Check stderr output for details"
                    )
                yield FakeResultMessage()

        def fake_tool(_name: str, _description: str, _param_types: dict[str, type]):
            def decorate(handler):
                return handler

            return decorate

        fake_module = types.ModuleType("claude_agent_sdk")
        fake_module.ClaudeAgentOptions = FakeClaudeAgentOptions
        fake_module.ClaudeSDKClient = FakeClaudeSDKClient
        fake_module.create_sdk_mcp_server = lambda **kwargs: kwargs
        fake_module.tool = fake_tool

        from agentbridge.agent import AgentRunner, _agent_sdk_session_id

        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {"claude_agent_sdk": fake_module}):
            kit = Path(tmp) / "kit"
            kit.mkdir()
            (kit / "capabilities.json").write_text("[]", encoding="utf-8")
            (kit / "guardrails").mkdir()
            (kit / "guardrails" / "permissions.json").write_text(json.dumps({"tools": {}}), encoding="utf-8")
            stable_session_id = _agent_sdk_session_id(kit, "web-session")

            runner = AgentRunner(kit, api_key="sk-test", session_id="web-session")
            response = runner.query_text("hello")
            runner.close()

        self.assertEqual(response, "fallback response")
        self.assertEqual(len(FakeClaudeSDKClient.instances), 2)
        self.assertEqual(FakeClaudeSDKClient.instances[0].disconnected, 1)
        attempted_session_ids = [str(options.kwargs.get("session_id")) for options in FakeClaudeAgentOptions.instances]
        self.assertNotEqual(attempted_session_ids[0], stable_session_id)
        self.assertNotEqual(attempted_session_ids[1], stable_session_id)
        self.assertNotEqual(attempted_session_ids[1], attempted_session_ids[0])
        uuid.UUID(attempted_session_ids[0])
        uuid.UUID(attempted_session_ids[1])
        self.assertEqual(getattr(FakeClaudeSDKClient.instances[1], "query_session_id"), "__not_passed__")

    def test_agent_runner_uses_env_model_and_custom_base_url_for_sdk_options(self):
        class FakeResultMessage:
            content = []
            usage = {"input_tokens": 1, "output_tokens": 1}
            result = "deepseek sdk response"

        class FakeClaudeAgentOptions:
            last_kwargs: dict[str, object] | None = None

            def __init__(self, **kwargs: object) -> None:
                FakeClaudeAgentOptions.last_kwargs = kwargs

        class FakeClaudeSDKClient:
            def __init__(self, options: FakeClaudeAgentOptions) -> None:
                self.options = options

            async def connect(self) -> None:
                pass

            async def disconnect(self) -> None:
                pass

            async def query(self, _prompt: str, session_id: str = "__not_passed__") -> None:
                self.query_session_id = session_id

            async def receive_response(self):
                yield FakeResultMessage()

        def fake_tool(_name: str, _description: str, _param_types: dict[str, type]):
            def decorate(handler):
                return handler

            return decorate

        fake_module = types.ModuleType("claude_agent_sdk")
        fake_module.ClaudeAgentOptions = FakeClaudeAgentOptions
        fake_module.ClaudeSDKClient = FakeClaudeSDKClient
        fake_module.create_sdk_mcp_server = lambda **kwargs: kwargs
        fake_module.tool = fake_tool

        from agentbridge.agent import AgentRunner

        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {"claude_agent_sdk": fake_module}):
            with patch.dict(
                os.environ,
                {
                    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
                    "ANTHROPIC_MODEL": "deepseek-v4-flash",
                },
            ):
                kit = Path(tmp) / "kit"
                kit.mkdir()
                (kit / "capabilities.json").write_text("[]", encoding="utf-8")
                (kit / "guardrails").mkdir()
                (kit / "guardrails" / "permissions.json").write_text(json.dumps({"tools": {}}), encoding="utf-8")

                runner = AgentRunner(kit, api_key="sk-test", session_id="web-session")
                response = runner.query_text("hello")
                runner.close()

        self.assertEqual(response, "deepseek sdk response")
        self.assertIsNone(FakeClaudeAgentOptions.last_kwargs.get("model"))
        self.assertEqual(FakeClaudeAgentOptions.last_kwargs["base_url"], "https://api.deepseek.com/anthropic")
        sdk_env = FakeClaudeAgentOptions.last_kwargs["env"]
        self.assertEqual(sdk_env["ANTHROPIC_API_KEY"], "sk-test")
        self.assertEqual(sdk_env["ANTHROPIC_BASE_URL"], "https://api.deepseek.com/anthropic")
        self.assertEqual(sdk_env["ANTHROPIC_MODEL"], "deepseek-v4-flash")
        sdk_settings = json.loads(FakeClaudeAgentOptions.last_kwargs["settings"])
        self.assertEqual(sdk_settings["env"]["ANTHROPIC_BASE_URL"], "https://api.deepseek.com/anthropic")
        self.assertEqual(sdk_settings["env"]["ANTHROPIC_MODEL"], "deepseek-v4-flash")
        self.assertNotIn("ANTHROPIC_API_KEY", sdk_settings["env"])

    def test_extract_agent_usage_supports_sdk_result_metadata(self):
        class FakeResultMessage:
            usage = {
                "input_tokens": 100,
                "output_tokens": 25,
                "cache_read_input_tokens": 40,
                "cache_creation_input_tokens": 10,
            }
            total_cost_usd = 0.0123
            duration_ms = 456
            num_turns = 3

        usage = _extract_agent_usage(FakeResultMessage())

        self.assertEqual(usage["input_tokens"], 100)
        self.assertEqual(usage["output_tokens"], 25)
        self.assertEqual(usage["total_tokens"], 125)
        self.assertEqual(usage["cache_read_input_tokens"], 40)
        self.assertEqual(usage["cost_usd"], 0.0123)
        self.assertEqual(usage["duration_ms"], 456)
        self.assertEqual(usage["turns"], 3)

    def test_parse_json_object_prefers_generation_payload(self):
        text = (
            "intermediate note {} "
            '{"project_analysis": {"summary": "done"}, '
            '"tool_enhancements": {"create_character": {"description": "Create character"}}, '
            '"risk_assessments": {"create_character": {"risk": "write"}}, '
            '"additional_tools": [], "system_prompt": "", "skills": {}}'
        )

        parsed = _parse_json_object(text, {})

        self.assertEqual(parsed["project_analysis"]["summary"], "done")
        self.assertIn("create_character", parsed["tool_enhancements"])

    def test_generate_all_reports_source_files_and_provider_call(self):
        messages: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "app.py"
            source.write_text("print('hello')\n", encoding="utf-8")

            gen = AIGenerator(api_key="sk-test", base_url="https://api.deepseek.com/anthropic", model="deepseek-v4-flash", progress=messages.append)

            async def _fake_ask(*_args, **_kwargs):
                return (
                    "{"
                    '"project_analysis": {}, '
                    '"tool_enhancements": {}, '
                    '"risk_assessments": {}, '
                    '"additional_tools": [], '
                    '"system_prompt": "", '
                    '"skills": {}'
                    "}"
                )

            gen._ask = _fake_ask  # type: ignore[method-assign]
            gen.generate_all([], "kit", input_paths=[source])

        self.assertTrue(any("Added source file to AI context" in message for message in messages))
        self.assertTrue(any("Sending AI analysis request" in message for message in messages))
        self.assertTrue(any("Received AI analysis response" in message for message in messages))

    def test_generate_all_uses_agentic_sdk_with_compatible_base_url(self):
        messages: list[str] = []

        class FakeTextBlock:
            def __init__(self, text: str) -> None:
                self.type = "text"
                self.text = text

        class FakeToolUseBlock:
            def __init__(self, name: str, tool_input: dict[str, str]) -> None:
                self.type = "tool_use"
                self.name = name
                self.input = tool_input

        class FakeAssistantMessage:
            def __init__(self, content: list[object]) -> None:
                self.role = "assistant"
                self.content = content

        class FakeClaudeAgentOptions:
            last_kwargs: dict[str, object] | None = None

            def __init__(self, **kwargs: object) -> None:
                FakeClaudeAgentOptions.last_kwargs = kwargs

        async def fake_query(prompt: str, options: object):
            self.assertIn("Project paths to inspect read-only", prompt)
            self.assertIsNotNone(options)
            yield FakeAssistantMessage([
                FakeToolUseBlock("Read", {"file_path": "app.py"}),
                FakeTextBlock(
                    json.dumps(
                        {
                            "project_analysis": {},
                            "tool_enhancements": {},
                            "risk_assessments": {},
                            "additional_tools": [],
                            "system_prompt": "",
                            "skills": {},
                        }
                    )
                ),
            ])

        fake_module = types.ModuleType("claude_agent_sdk")
        fake_module.ClaudeAgentOptions = FakeClaudeAgentOptions
        fake_module.query = fake_query

        base_url = "https://api.deepseek.com/anthropic"
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {"claude_agent_sdk": fake_module}), patch.dict(os.environ, {"ANTHROPIC_BASE_URL": base_url}, clear=False):
            root = Path(tmp)
            (root / "app.py").write_text("print('hello')\n", encoding="utf-8")

            gen = AIGenerator(api_key="sk-test", progress=messages.append, analysis_mode="agentic")
            result = gen.generate_all([], "kit", input_paths=[root])

        self.assertEqual(FakeClaudeAgentOptions.last_kwargs["cwd"], str(root.resolve()))
        self.assertEqual(FakeClaudeAgentOptions.last_kwargs["base_url"], base_url)
        self.assertIsNone(FakeClaudeAgentOptions.last_kwargs.get("model"))
        sdk_settings = json.loads(FakeClaudeAgentOptions.last_kwargs["settings"])
        self.assertEqual(sdk_settings["env"]["ANTHROPIC_BASE_URL"], base_url)
        self.assertEqual(sdk_settings["env"]["ANTHROPIC_MODEL"], "claude-sonnet-4-20250514")
        self.assertEqual(FakeClaudeAgentOptions.last_kwargs["tools"], ["Read", "Grep"])
        self.assertEqual(FakeClaudeAgentOptions.last_kwargs["allowed_tools"], ["Read", "Grep"])
        self.assertIn("Agent", FakeClaudeAgentOptions.last_kwargs["disallowed_tools"])
        self.assertTrue(any("Using Claude Agent SDK agentic analysis" in message for message in messages))
        self.assertTrue(any(base_url in message for message in messages))
        self.assertTrue(any("Claude Agent SDK reading file: app.py" in message for message in messages))
        self.assertTrue(any("Claude Agent SDK generated batch analysis JSON" in message for message in messages))
        self.assertEqual(result["system_prompt"], "")

    def test_agentic_backend_detection_does_not_import_claude_agent_sdk(self):
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name == "claude_agent_sdk":
                raise AssertionError("claude_agent_sdk should not be imported during backend detection")
            return original_import(name, *args, **kwargs)

        with patch("importlib.util.find_spec", return_value=object()), patch("builtins.__import__", side_effect=guarded_import):
            gen = AIGenerator(api_key="sk-test", analysis_mode="agentic")

        self.assertEqual(gen._backend, "agent-sdk")

    def test_agentic_progress_includes_tool_results_and_hidden_thinking(self):
        class FakeToolResultBlock:
            def __init__(self) -> None:
                self.type = "tool_result"
                self.tool_use_id = "call-1"
                self.content = {"path": "app.py", "content": "print('hello')\n"}

        class FakeThinkingBlock:
            def __init__(self) -> None:
                self.type = "thinking"
                self.text = "private reasoning"

        class FakeAssistantMessage:
            def __init__(self) -> None:
                self.role = "assistant"
                self.content = [FakeThinkingBlock(), FakeToolResultBlock()]

        messages: list[str] = []
        with patch("importlib.util.find_spec", return_value=object()):
            gen = AIGenerator(api_key="sk-test", progress=messages.append, analysis_mode="agentic")
        gen._report_agent_sdk_message(FakeAssistantMessage())

        self.assertTrue(any("internal reasoning step completed" in message for message in messages))
        self.assertTrue(any("tool result received" in message and "path=app.py" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
