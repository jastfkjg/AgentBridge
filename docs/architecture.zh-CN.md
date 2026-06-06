# 架构

AgentBridge 采用 AI agent 优先的生成流水线，同时保留确定性扫描器作为候选证据收集层。

## 流程

1. 候选发现器扫描 OpenAPI、GraphQL、SQL、路由和数据库定义。
2. 没有 LLM 时，确定性生成器直接产出可运行 kit；有 LLM 时，AI 分析 agent 读取项目代码和候选证据。
3. AI agent 产出项目分析、风险推理、增强能力、skills 和 prompts。
4. 生成器写入 `agentbridge-kit/v1` 协议目录。
5. `agentbridge serve` 将套件作为 stdio MCP Server 暴露给 Claude、Codex 或其他 MCP client。
6. 运行时工具在执行宿主系统 adapter 前，先执行 guardrails 和 dry-run 校验。

## 第一阶段 MVP

当前 MVP 聚焦一条最短闭环：

```bash
agentbridge generate openapi.json --output .agentbridge/openapi-kit --no-ai
agentbridge serve .agentbridge/openapi-kit --base-url http://localhost:8080 --execute
```

这条路径不依赖 LLM。OpenAPI 操作会被标准化为能力，套件会生成 MCP 工具定义、guardrails、dry-run plan、skills 和 system prompt。`serve` 默认 dry-run；只有显式 `--execute` 才会通过 HTTP adapter 调用目标系统。

## 为什么仍然保留规则

规则适合廉价、确定性地收集证据，也能支撑无 LLM 的 OpenAPI 到 MCP Server 快速路径。但它不应该被当成最终业务模型。真正理解 controller/service 行为、工作流意图、副作用和代码隐含操作的是 AI 分析层。

## 运行时边界

执行边界分两层：

- 默认模式：MCP tool call 返回计划调用，不触发目标系统副作用。
- 执行模式：`--execute` 开启真实 HTTP 调用，但高风险工具仍必须传入 `confirmed: true`。

## 安全边界

生成阶段可以推断工具，但运行时执行必须服从 `guardrails/permissions.json`。生成的助手不能在没有明确人工确认的情况下执行破坏性或外部副作用操作。

