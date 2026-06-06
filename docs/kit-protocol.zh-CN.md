# AgentBridge 套件协议

当前协议：`agentbridge-kit/v1`

AgentBridge 套件是为已有系统生成的稳定目录。Agent 运行时、MCP Server、SDK Adapter、CI 检查和 dry-run 工具都应该先读取 `manifest.json`，再通过 `outputs` 字段定位其他文件。

## 必需目录结构

```text
agent-kit/
  manifest.json
  capabilities.json
  analysis/
    rule_signals.json
    agent_analysis.json
  spec/
    kit-protocol.md
  tools/
    mcp_tools.json
    openai_tools.json
    claude_tools.json
    vercel_ai_tools.ts
  skills/
    *.md
  prompts/
    system.md
  resources/
    schema.json
  guardrails/
    permissions.json
  tests/
    tool_invocation_tests.json
    test_generated_tools.py
  dry_run_plan.json
```

## 语义

- `analysis/rule_signals.json` 保存来自 schema 和源码路由扫描器的候选证据。
- `analysis/agent_analysis.json` 保存 AI agent 或确定性生成器对项目的理解、工作流、假设、副作用和风险推理。
- `capabilities.json` 是标准能力列表，用于生成工具和运行时执行。
- `tools/mcp_tools.json` 可被 `agentbridge serve` 暴露为 stdio MCP tools。
- `guardrails/permissions.json` 是运行时安全判断的权威来源。
- `dry_run_plan.json` 描述计划调用，不执行真实副作用。

## MCP Server 运行时

`agentbridge serve <kit>` 会读取 `manifest.json`、`capabilities.json` 和 `guardrails/permissions.json`，并通过 stdio JSON-RPC 暴露 MCP `tools/list` 与 `tools/call`。

- 默认不执行真实请求，只返回 dry-run 计划。
- 传入 `--execute` 后，HTTP transport 工具会调用 `--base-url` 指向的目标系统。
- `destructive` 和 `external_side_effect` 工具必须由调用方传入 `confirmed: true`。
- 当前执行 adapter 以 OpenAPI/HTTP transport 为主；GraphQL、数据库和更多 SDK adapter 后续扩展。

## 兼容性

消费者必须校验 `manifest.json` 中的 `protocol` 字段。小版本可以增加可选文件，但 `agentbridge-kit/v1` 的必需路径应保持稳定。

