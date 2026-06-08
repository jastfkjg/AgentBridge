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
  clients/
    mcp-client-configs.json
    README.md
  dry_run_plan.json
```

## 语义

- `analysis/rule_signals.json` 保存来自 schema 和源码路由扫描器的候选证据。
- `analysis/agent_analysis.json` 保存 AI agent 或确定性生成器对项目的理解、工作流、假设、副作用和风险推理。
- `capabilities.json` 是标准能力列表，用于生成工具和运行时执行。
- `tools/mcp_tools.json` 可被 `agentbridge serve` 暴露为 stdio MCP tools。
- `guardrails/permissions.json` 是运行时安全判断的权威来源。
- `clients/mcp-client-configs.json` 保存 Claude/Codex/通用 MCP 接入配置片段。
- `dry_run_plan.json` 描述计划调用，不执行真实副作用。

## 可选分析检查点

大型项目生成时还可能写入：

- `analysis/resume_state.json`：当前批次计划、已完成批次、剩余批次和 partial/complete 状态。
- `analysis/batches/*.json`：已完成的 AI 增强批次输出，供 `--resume` 使用。

这些文件是增量文件，不是 `agentbridge-kit/v1` 消费者的必需文件，但工具可以读取它们来展示进度或继续未完成的 AI 增强。

## MCP Server 运行时

`agentbridge serve <kit>` 会读取 `manifest.json`、`capabilities.json` 和 `guardrails/permissions.json`，并通过 stdio JSON-RPC 暴露 MCP `tools/list` 与 `tools/call`。

- 默认不执行真实请求，只返回 dry-run 计划。
- 传入 `--execute` 后，HTTP transport 工具会调用 `--base-url` 指向的目标系统。
- `destructive` 和 `external_side_effect` 工具必须由调用方传入 `confirmed: true`。
- 当前执行 adapter 以 OpenAPI/HTTP transport 为主；GraphQL、数据库和更多 SDK adapter 后续扩展。

## Chat 运行时

`agentbridge chat <kit>` 和 `agentbridge web <kit>` 消费同一套 kit 文件和运行时 guardrails。聊天记忆保存最近对话和待确认操作，不属于稳定协议必需文件，默认位于 `<kit>/.agentbridge-chat-memory.json`。

## 目标项目边界

kit 是唯一生成产物。AgentBridge 的生成器和消费者在发现或生成阶段不得写入目标项目。输入项目只作为只读证据，输出产物只位于用户选择的 kit 目录。

## 兼容性

消费者必须校验 `manifest.json` 中的 `protocol` 字段。小版本可以增加可选文件，但 `agentbridge-kit/v1` 的必需路径应保持稳定。
