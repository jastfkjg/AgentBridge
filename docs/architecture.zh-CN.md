# 架构

AgentBridge 将现有项目或系统转换为可被 Claude 控制的工具层。生成流水线以 AI agent 分析为主，同时保留确定性扫描器作为 API、Schema、路由、数据库定义和其他系统信号的低成本证据层。

## 标准流程

```text
现有项目/系统
  -> 解析项目 / API / 数据库 / GraphQL / 后台任务证据
  -> 标准化能力 capabilities
  -> Agent Integration Kit
  -> Claude Agent SDK / MCP / Web Chat
  -> 受控操作 API / 数据库 / GraphQL / 后台任务
```

1. 候选发现器扫描 OpenAPI、GraphQL、SQL、源码路由、数据库定义和其他系统证据。OpenAPI schema 会展开本地 `$ref` 并归一化，Python 路由优先使用 AST 证据，TypeScript 和 Java 路由会记录结构化 source-tree 证据。
2. 对项目目录，AI 分析 agent 优先使用 Claude Agent SDK 进行 agentic 探索，读取项目代码和候选证据，并可通过分批检查点支持 resume；对 schema-only 输入，`--no-ai` 可以产出确定性可运行 kit。
3. AI agent 产出项目分析、风险推理、增强能力、skills、prompts，以及大型项目的可选分批检查点。
4. 生成器写入 `agentbridge-kit/v1` 协议目录。这个 kit 是已解析系统能力与 agent-facing 工具入口之间的版本化契约，包含证据链接、confidence score、人类可读分析报告和 guardrail policy。
5. `agentbridge serve` 将 kit 作为 stdio MCP Server 暴露给 Claude、Codex 或其他 MCP client。
6. `agentbridge chat` 和 `agentbridge web` 在同一套 kit runtime 上提供 Claude Agent Chat 控制入口。
7. 运行时工具在执行宿主系统 adapter 前，先执行 guardrails 和 dry-run 校验。

## 当前 MVP

当前最短闭环是 OpenAPI/HTTP 到可运行的 MCP 或 Chat 控制入口：

```bash
agentbridge generate openapi.json --output .agentbridge/openapi-kit --no-ai
agentbridge serve .agentbridge/openapi-kit --base-url http://localhost:8080 --execute
```

这条 schema-only 路径不依赖 LLM。OpenAPI 操作会被标准化为能力，kit 会生成 MCP 工具定义、guardrails、dry-run plan、skills 和 system prompt。`serve` 默认 dry-run；只有显式 `--execute` 才会通过 HTTP adapter 调用目标系统。

GraphQL、数据库和后台任务证据目前可以被发现并表示为能力。真实执行当前主要覆盖 HTTP/OpenAPI transport；后续会扩展更多执行 adapter。

Kit 质量检查可以进入 CI：`agentbridge diff` 比较两次生成结果，`agentbridge generate --check` 在输出 Kit 过期时失败，`agentbridge validate --migrate` 可以补齐 `v1` 增量文件且不提升协议版本。

## 为什么仍然保留规则

规则适合廉价、确定性地收集证据，也能支撑无 LLM 的 OpenAPI 到 MCP Server 快速路径。但它不应该被当成最终业务模型。真正理解 controller/service 行为、工作流意图、副作用和代码隐含操作的是 AI 分析层。

## 大型项目分析

AgentBridge 会将大型项目分析拆成按优先级排序的批次。第一批优先覆盖主能力，然后 CLI 可以询问是否继续增强剩余批次。Claude Agent SDK 批次会把只读工具调用、文件读取、代码搜索和工具返回实时输出到 CLI 进度与 `generation_status.json`。批次进度记录在 `analysis/resume_state.json` 和 `analysis/batches/*.json` 下，`--resume` 会跳过已经完成的批次。如果 Claude Agent SDK 计划或批次卡住，AgentBridge 会按超时切换到本地基础项目分析，先生成可用 kit，并在之后 AI 后端可用时重试 fallback 或 local-basic 检查点。

`agentbridge enhance <kit> <paths>` 会原地更新已有 Kit。该命令强制使用 Claude Agent SDK，将当前扫描证据与已有 AI 推断能力合并，合并重复 transport 操作，并重新生成协议文件。

运行时加载旧 Kit 时也会兼容处理 `_2`、`_3` 等历史数字后缀：相同 transport 操作会合并，不同操作会获得语义化名称，并同步重映射 Guardrail。

重新生成时会保留用户手写的 `prompts/system.md`、`skills/*.md` 和 guardrail policy/tool 覆盖，并把保留记录写入 `analysis/preserved_user_files.json`。

`--analysis-mode auto` 会在安装了 `claude-agent-sdk` 时优先使用 Claude Agent SDK，包括 `ANTHROPIC_BASE_URL` 指向 DeepSeek 等 Anthropic 兼容端点的情况。`--analysis-mode agentic` 要求走 SDK 路线，并会把兼容端点继续传给 SDK；`--analysis-mode prompt` 则强制使用直接 prompt 生成。

## 项目写入边界

AgentBridge 在发现和生成阶段不得修改目标项目。所有生成产物只能写入调用方指定的输出目录。如果输出目录位于被扫描项目内部，它必须是 `.agentbridge/` 或 `agentbridge-kit/` 这样的专用集成目录。

## 运行时边界

执行边界分两层：

- 默认模式：MCP 和 Chat tool call 返回计划调用，不触发目标系统副作用。
- 执行模式：`--execute` 开启真实 adapter 调用，但先执行生成策略：read 可执行，write 需要确认，destructive 默认拒绝，external-side-effect 需要确认。

聊天入口额外提供会话记忆和 human-in-the-loop 确认。高风险操作会作为 pending call 保存，直到用户确认或取消。

## 安全边界

生成阶段可以推断工具，但运行时执行必须服从 `guardrails/permissions.json`。运行时失败会返回结构化错误，审计日志会脱敏，生成的助手不能执行策略拒绝的操作，除非操作者明确修改 Kit 策略。
