# 架构

AgentBridge 采用 AI agent 优先的生成流水线，同时保留确定性扫描器作为候选证据收集层。

## 流程

1. 候选发现器扫描 OpenAPI、GraphQL、SQL、路由和数据库定义。
2. 对项目目录，AI 分析 agent 优先使用 Claude Agent SDK 进行 agentic 探索，读取项目代码和候选证据，并可通过分批检查点支持 resume；对 schema-only 输入，`--no-ai` 可以产出确定性可运行 kit。
3. AI agent 产出项目分析、风险推理、增强能力、skills、prompts，以及大型项目的可选分批检查点。
4. 生成器写入 `agentbridge-kit/v1` 协议目录。
5. `agentbridge serve` 将套件作为 stdio MCP Server 暴露给 Claude、Codex 或其他 MCP client。
6. `agentbridge chat` 和 `agentbridge web` 在同一套 kit runtime 上提供面向用户的聊天入口。
7. 运行时工具在执行宿主系统 adapter 前，先执行 guardrails 和 dry-run 校验。

## 第一阶段 MVP

当前 MVP 聚焦一条最短闭环：

```bash
agentbridge generate openapi.json --output .agentbridge/openapi-kit --no-ai
agentbridge serve .agentbridge/openapi-kit --base-url http://localhost:8080 --execute
```

这条 schema-only 路径不依赖 LLM。OpenAPI 操作会被标准化为能力，套件会生成 MCP 工具定义、guardrails、dry-run plan、skills 和 system prompt。`serve` 默认 dry-run；只有显式 `--execute` 才会通过 HTTP adapter 调用目标系统。

## 为什么仍然保留规则

规则适合廉价、确定性地收集证据，也能支撑无 LLM 的 OpenAPI 到 MCP Server 快速路径。但它不应该被当成最终业务模型。真正理解 controller/service 行为、工作流意图、副作用和代码隐含操作的是 AI 分析层。

## 大型项目分析

AgentBridge 会将大型项目分析拆成按优先级排序的批次。第一批优先覆盖主能力，然后 CLI 可以询问是否继续增强剩余批次。批次进度记录在 `analysis/resume_state.json` 和 `analysis/batches/*.json` 下，`--resume` 会跳过已经完成的批次。如果 Claude Agent SDK 计划或批次卡住，AgentBridge 会按超时兜底，写入确定性 fallback 批次，先生成 partial kit，并在下一次 `--resume` 时重试 fallback 批次。

`--analysis-mode auto` 会在安装了 `claude-agent-sdk` 时优先使用 Claude Agent SDK，包括 `ANTHROPIC_BASE_URL` 指向 DeepSeek 等 Anthropic 兼容端点的情况。`--analysis-mode agentic` 要求走 SDK 路线，`--analysis-mode prompt` 则强制使用直接 prompt 生成。

## 项目写入边界

AgentBridge 在发现和生成阶段不得修改目标项目。所有生成产物只能写入调用方指定的输出目录。如果输出目录位于被扫描项目内部，它必须是 `.agentbridge/` 或 `agentbridge-kit/` 这样的专用集成目录。

## 运行时边界

执行边界分两层：

- 默认模式：MCP tool call 返回计划调用，不触发目标系统副作用。
- 执行模式：`--execute` 开启真实 HTTP 调用，但高风险工具仍必须传入 `confirmed: true`。

聊天入口额外提供会话记忆和 human-in-the-loop 确认。高风险操作会作为 pending call 保存，直到用户确认或取消。

## 安全边界

生成阶段可以推断工具，但运行时执行必须服从 `guardrails/permissions.json`。生成的助手不能在没有明确人工确认的情况下执行破坏性或外部副作用操作。
