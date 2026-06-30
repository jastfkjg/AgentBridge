# MCP 运行时

MCP 是 AgentBridge 工具层的一种暴露方式。生成的 Agent Integration Kit 可以作为 stdio MCP tools 对外提供，让 Claude、Codex 或任何 MCP-compatible client 能够检查、dry-run，并在安全策略保护下操作已解析系统能力。

## 快速开始

不配置 LLM 也可以生成 schema-only kit：

```bash
agentbridge generate openapi.json --output .agentbridge/openapi-kit --no-ai
```

启动 stdio MCP Server：

```bash
agentbridge serve .agentbridge/openapi-kit
```

OpenAPI 路径默认只返回 dry-run 计划，不会调用目标系统。完整项目目录理解应配置 AI 后端，让 AgentBridge 基于代码语义进行推理，扫描器输出只作为辅助证据。

连接真实 HTTP 系统：

```bash
agentbridge serve .agentbridge/openapi-kit \
  --base-url http://localhost:8080 \
  --bearer-env API_TOKEN \
  --execute
```

生成客户端配置片段：

```bash
agentbridge mcp-config .agentbridge/openapi-kit \
  --base-url http://localhost:8080 \
  --bearer-env API_TOKEN \
  --execute
```

也可以传入额外 header：

```bash
agentbridge serve .agentbridge/openapi-kit \
  --base-url http://localhost:8080 \
  --header "X-Tenant=demo" \
  --header "X-Request-Source=agentbridge" \
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

## 运行模式

| 模式 | 命令 | 行为 |
|---|---|---|
| Dry-run | `agentbridge serve <kit>` | MCP tool call 返回计划调用，不触发目标系统副作用 |
| Execute | `agentbridge serve <kit> --base-url <url> --execute` | HTTP transport 工具会调用目标系统 |
| Execute GraphQL | `agentbridge serve <kit> --graphql-endpoint <url> --execute` | GraphQL 工具会 POST 生成的 query/mutation 和 variables |
| Execute SQL | `agentbridge serve <kit> --database-url sqlite:///tmp/app.db --execute` | SQL 工具只执行带自动 `LIMIT` 的只读 `SELECT` |
| Execute gRPC | `agentbridge serve <kit> --grpc-target host:port --execute` | gRPC 工具通过 `grpcurl` 发送 JSON message |

## 安全规则

- `serve` 默认 dry-run，这是安全默认值。
- 只有显式传入 `--execute` 才会发起真实运行时调用。
- 生成策略默认是 read 自动执行、write 需要确认、destructive 拒绝、external-side-effect 需要确认。
- `write` 和 `external_side_effect` 工具必须由 MCP caller 在参数中传入 `confirmed: true`。`destructive` 工具默认拒绝，除非操作者修改 Kit 策略。
- Bearer token 和 header 只通过运行时参数传入。推荐使用 `--bearer-env API_TOKEN`，让配置文件只保存环境变量名。
- `--read-only` 会阻断 write/destructive/external-side-effect 工具。
- `--deny-risk` 可禁用一个或多个风险等级。
- `--allow-tool` 可限制运行时只允许指定工具。
- `--audit-log` 会写入 JSONL 工具调用审计日志，包含 user、session、model、tool call id、确认来源、outcome、risk 和脱敏参数。
- dry-run 响应会包含 transport 专属请求预览、脱敏后的密钥信息和风险理由。
- 运行时失败使用结构化错误码：`permission_denied`、`schema_mismatch`、`http_error`、`timeout`、`adapter_error`。

连接 agent 前建议先运行：

```bash
agentbridge validate .agentbridge/openapi-kit
agentbridge doctor .agentbridge/openapi-kit --execute --base-url http://localhost:8080
```

## HTTP 映射

OpenAPI 中的 HTTP transport 会被映射为真实请求：

- path 参数：`/projects/{project_id}/chapters` + `{"project_id":"p1"}` -> `/projects/p1/chapters`
- GET/HEAD/OPTIONS 的剩余参数进入 query string
- POST/PUT/PATCH/DELETE 的剩余参数作为 JSON body
- `--bearer-token` 会直接生成 `Authorization: Bearer ...`
- `--bearer-env API_TOKEN` 会在运行时从环境变量读取 token，生成客户端配置时更推荐。
- `--header NAME=VALUE` 可以重复传入

## 其他运行时 Adapter

- GraphQL 工具来自 schema 中的 `Query` 和 `Mutation` 字段。运行时会生成 operation document，把 capability 参数映射到 GraphQL variables，并 POST 到 `--graphql-endpoint` 或 `--base-url`。
- SQL 工具来自 `CREATE TABLE`，只生成 read-only `list_*` 能力。运行时只执行 `SELECT`，支持可选 `id` 过滤，并自动限制 `limit` 上限。
- gRPC 工具来自 `.proto` service/method 和 message 字段。真实执行依赖 `grpcurl`；dry-run 预览不会连接目标系统。
- Python plugin 工具必须有显式标记，例如 `AGENTBRIDGE_PLUGIN = True` 或 `agentbridge_discover()`。插件模块可提供 `dry_run(capability, args, config)` 和 `execute(capability, args, config)`。

## MCP 能力

`agentbridge serve` 通过 stdio JSON-RPC 暴露：

- `initialize`
- `tools/list`
- `tools/call`

`tools/list` 会把 `capabilities.json` 中的能力转换为 MCP tools。需要人工确认的工具会额外暴露 `confirmed` 参数，方便 client 在调用时表达明确授权。

## 当前边界

当前执行支持：

- 已支持：OpenAPI/HTTP、GraphQL、SQLite read-only SQL、基于 `grpcurl` 的 gRPC、Python plugin adapter、dry-run、结构化错误、审计脱敏和确认参数。
- 后续扩展：更广的数据库方言、后台任务 adapter 和更强的 agent planning。
