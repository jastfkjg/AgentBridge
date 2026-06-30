# AgentBridge

AgentBridge 将现有项目和系统自动解析为可通过 Claude Agent Chat 控制的版本化工具层。它会收集系统证据，标准化为能力模型，打包成 Agent Integration Kit，并通过 Claude Agent SDK、MCP、终端 Chat 和 Web Chat 暴露出来。

```text
现有项目/系统
  -> 解析项目 / API / 数据库 / GraphQL / 后台任务证据
  -> 标准化能力 capabilities
  -> Agent Integration Kit
  -> Claude Agent SDK / MCP / Web Chat
  -> 受控操作 API / 数据库 / GraphQL / 后台任务
```

[English](README.md)

## 核心能力

- 使用 Claude Agent SDK 进行 AI 优先的项目分析。
- 从 OpenAPI、GraphQL、SQL、gRPC proto、显式 Python plugin 和源码路由收集候选能力。
- 生成稳定的 `agentbridge-kit/v1` 协议目录，包含能力、工具、提示词、技能、资源 Schema、Guardrail、Dry-run 计划、客户端配置和测试。
- 从同一套能力模型生成 Claude Agent SDK、MCP、Claude、OpenAI 和 Vercel AI 工具定义。
- 通过 Web 或终端 Chat 在生成的工具层上控制系统能力。
- 默认 Dry-run，运行时策略、结构化错误、审计脱敏和 human-in-the-loop 授权共同保护真实执行。
- 更强的项目理解：OpenAPI `$ref`/JSON Schema 归一化、基于 AST 的 Python 路由发现、TypeScript/Java 结构化源码扫描、证据链接、confidence score 和人类可读分析报告。
- 面向 CI 的 Kit 质量能力：`agentbridge diff`、`generate --check`、增量迁移，以及重新生成时保留用户手写 prompts、skills、guardrails。
- 支持会话历史、Web Chat 流式响应、工具调用时间线、点击调用工具、必填参数提示、文件上传和 AI Token/成本信息。
- 项目变化后可在已有 Kit 基础上继续分析。

当前运行 adapter 覆盖 HTTP/OpenAPI、GraphQL POST、SQLite read-only SQL SELECT、基于 `grpcurl` 的 gRPC，以及显式标记的 Python plugin dry-run/execute hook。所有 transport 默认仍是 dry-run。

## 安装

```bash
pip install "agbr[agent]"
```

项目目录分析和 `agentbridge enhance` 需要：

```bash
export ANTHROPIC_API_KEY="..."
```

可选的 Anthropic 兼容端点：

```bash
export ANTHROPIC_BASE_URL="https://api.example.com/anthropic"
export ANTHROPIC_MODEL="your-model"
```

## 生成 Kit

使用 Claude Agent SDK 分析项目目录：

```bash
agentbridge generate ./my-system \
  --output .agentbridge/my-system-kit \
  --analysis-mode agentic
```

仅根据 Schema 做确定性生成：

```bash
agentbridge generate ./openapi.json \
  --output .agentbridge/openapi-kit \
  --no-ai
```

验证 Kit：

```bash
agentbridge validate .agentbridge/my-system-kit
```

检查已有 Kit 是否过期但不重写目录：

```bash
agentbridge generate ./openapi.json \
  --output .agentbridge/openapi-kit \
  --no-ai \
  --check
```

比较两次生成结果：

```bash
agentbridge diff .agentbridge/old-kit .agentbridge/new-kit
```

## 增强已有 Kit

重新分析当前项目，并原地更新已有 Kit：

```bash
agentbridge enhance .agentbridge/my-system-kit ./my-system
```

该命令强制使用 Claude Agent SDK。已有 AI 推断能力会作为基线保留，当前项目会重新扫描，重复端点会被合并，变化或新增能力会重新生成。

复用有效的批次检查点：

```bash
agentbridge enhance .agentbridge/my-system-kit ./my-system --resume
```

## 启动 Web Chat

```bash
agentbridge web .agentbridge/my-system-kit --port 8765
```

打开命令输出的 URL。Web 页面是面向已解析系统能力的 Claude Agent Chat 控制入口，支持：

- 切换 Dry-run 和真实系统模式。
- Base URL 校验和连通测试。
- 查看和编辑 `guardrails/permissions.json` 的权限策略。
- 点击工具后自动填入 `/run` 命令和必填参数。
- 高风险操作显示明确的授权/取消按钮。
- SSE 流式响应、工具调用时间线、中断按钮、最近会话、文件上传、Markdown 响应和 Claude Agent SDK model/token/cost 用量。

真实系统模式仍会执行生成的 Guardrail 和确认规则。默认策略是：read 可在执行模式下直接运行，write 必须确认，destructive 默认拒绝，external side effect 必须确认。

启动时可传入运行凭据：

```bash
agentbridge web .agentbridge/my-system-kit \
  --base-url http://localhost:8080 \
  --bearer-env API_TOKEN \
  --execute
```

## 启动终端 Chat

```bash
agentbridge chat .agentbridge/my-system-kit
```

常用命令：

```text
/tools
/use
/run <tool> key=value
/mode dry-run
/mode execute http://localhost:8080
/connect http://localhost:8080
/usage
/history
```

`/use` 提供编号工具选择，并逐项询问必填参数。高风险操作会显示 Authorize/Cancel 选项。

## 启动 MCP Server

将生成的 Agent Integration Kit 暴露为 MCP tools。

Dry-run：

```bash
agentbridge serve .agentbridge/my-system-kit
```

真实 HTTP 执行：

```bash
agentbridge serve .agentbridge/my-system-kit \
  --base-url http://localhost:8080 \
  --bearer-env API_TOKEN \
  --execute
```

GraphQL、SQL 和 gRPC 工具使用对应的运行目标：

```bash
agentbridge serve .agentbridge/my-system-kit \
  --graphql-endpoint http://localhost:8080/graphql \
  --database-url sqlite:///tmp/app.db \
  --grpc-target 127.0.0.1:50051 \
  --execute
```

生成客户端配置：

```bash
agentbridge mcp-config .agentbridge/my-system-kit --write
```

## 默认安全规则

- Dry-run 不会执行目标系统操作。
- read 工具可在执行模式下运行。
- write 工具必须明确确认。
- destructive 工具默认被生成策略拒绝，除非操作者修改 Kit 策略。
- external-side-effect 工具必须明确确认。
- 运行时失败统一返回 `permission_denied`、`schema_mismatch`、`http_error`、`timeout`、`adapter_error` 等结构化错误码。
- 审计日志会脱敏 password、token、cookie、authorization header、API key 和 secret。
- 切换运行模式会清除待授权操作。
- 项目分析只读，生成文件只写入指定 Kit 目录。
- 密钥属于运行时输入，不能写入生成的 Kit。

## 主要命令

| 命令 | 作用 |
| --- | --- |
| `discover <paths>` | 输出确定性候选能力 |
| `generate <paths> -o <kit>` | 生成可被 Claude 控制的 Agent Integration Kit |
| `generate <paths> -o <kit> --check` | 检查现有 Kit 是否与重新生成结果一致 |
| `diff <old-kit> <new-kit>` | 比较能力、风险、schema、transport 和 guardrail 变化 |
| `enhance <kit> <paths>` | 使用 Claude Agent SDK 更新已有 Kit |
| `validate <kit>` | 验证 Kit 协议和安全约束 |
| `validate <kit> --migrate` | 验证前应用 `v1` 增量迁移 |
| `doctor <kit>` | 检查运行配置 |
| `web <kit>` | 在工具层上启动浏览器 Chat |
| `chat <kit>` | 在工具层上启动终端 Chat |
| `serve <kit>` | 将 Kit 暴露为 stdio MCP Server |
| `dry-run <kit> <tool>` | 预览单次工具调用 |
| `mcp-config <kit>` | 生成 MCP 客户端配置 |

## 详细文档

- [架构](docs/architecture.zh-CN.md)
- [Chat 与 Web UI](docs/chat.zh-CN.md)
- [MCP 运行时](docs/mcp-server.zh-CN.md)
- [Kit 协议](docs/kit-protocol.zh-CN.md)

## 开发验证

```bash
PYTHONPATH=src python -m unittest discover -s tests
PYTHONPATH=src python -m compileall src tests
```

## License

MIT
