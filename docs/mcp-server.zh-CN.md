# OpenAPI 到可运行 MCP Server

AgentBridge 第一阶段 MVP 的目标是：从现有 OpenAPI schema 快速生成一个可被 Claude、Codex 或其他 MCP client 使用的操作环境。

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

## 运行模式

| 模式 | 命令 | 行为 |
|---|---|---|
| Dry-run | `agentbridge serve <kit>` | MCP tool call 返回计划调用，不触发目标系统副作用 |
| Execute | `agentbridge serve <kit> --base-url <url> --execute` | HTTP transport 工具会调用目标系统 |

## 安全规则

- `serve` 默认 dry-run，这是安全默认值。
- 只有显式传入 `--execute` 才会发起 HTTP 请求。
- `destructive` 和 `external_side_effect` 工具必须由 MCP caller 在参数中传入 `confirmed: true`。
- Bearer token 和 header 只通过运行时参数传入。推荐使用 `--bearer-env API_TOKEN`，让配置文件只保存环境变量名。
- `--read-only` 会阻断 write/destructive/external-side-effect 工具。
- `--deny-risk` 可禁用一个或多个风险等级。
- `--allow-tool` 可限制运行时只允许指定工具。
- `--audit-log` 会写入 JSONL 工具调用审计日志。
- HTTP transport 的 dry-run 响应会包含请求预览：method、URL、脱敏 headers、body 和风险理由。

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

## MCP 能力

`agentbridge serve` 通过 stdio JSON-RPC 暴露：

- `initialize`
- `tools/list`
- `tools/call`

`tools/list` 会把 `capabilities.json` 中的能力转换为 MCP tools。高风险工具会额外暴露 `confirmed` 参数，方便 client 在调用时表达人工确认。

## 第一阶段边界

当前 MVP 重点覆盖 OpenAPI/HTTP：

- 已支持：OpenAPI discovery、kit 生成、stdio MCP server、HTTP GET/POST/PUT/PATCH/DELETE 执行、dry-run、确认参数。
- 后续扩展：GraphQL adapter、数据库 adapter 和更强的 agent planning。
