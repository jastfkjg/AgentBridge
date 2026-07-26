# 聊天入口

AgentBridge 的聊天入口是在生成的 Agent Integration Kit 之上提供的 Claude Agent 控制界面。CLI Chat 和 Web Chat 复用同一套本地运行时：

- 从 `capabilities.json` 读取已解析系统能力
- 从 `guardrails/permissions.json` 读取安全策略
- 通过 AgentBridge runtime 调用工具
- 通过 `--execute` 选择是否真实调用运行时 adapter
- 支持会话记忆和策略要求确认的待授权操作

目标是让用户通过 Claude 驱动的 Agent 对话，检查、规划、dry-run，并在 Guardrail 保护下安全操作已有系统。

## CLI Chat

```bash
agentbridge chat .agentbridge/openapi-kit
```

默认是 dry-run 模式，只返回计划调用，不触发目标系统副作用。

```bash
agentbridge chat .agentbridge/openapi-kit \
  --base-url http://localhost:8080 \
  --bearer-env API_TOKEN \
  --execute \
  --audit-log .agentbridge/audit.jsonl \
  --user alice \
  --session demo
```

聊天中可用：

```text
/tools
/run list_chapter project_id=p1
create_chapter project_id=p1 title="Opening"
delete_character project_id=p1 character_id=c1
confirm
cancel
/history
```

被策略标记为 `confirm` 的操作会先暂停，并展示计划调用、风险理由、请求 URL、脱敏 headers、body 和参数。输入 `confirm` 继续，输入 `cancel` 清除待确认操作。生成默认策略允许 read，write 和 external side effect 需要确认，destructive 默认拒绝。

## System Control Console

```bash
agentbridge web .agentbridge/openapi-kit --port 8765
```

打开命令输出的 URL。Web 首页是响应式 System Control Console，包含七个可通过 URL hash 直接访问的一级工作区：

- **Chat**（`#chat`）：保留 Claude Agent 对话、SSE 流式响应、文件上传、中断、最近会话，以及高风险操作的 Authorize/Cancel。
- **Tools**（`#tools`）：按名称和风险搜索生成工具；点击 **Prepare** 只会把带必填参数的 `/run` 命令填入 Chat，不会直接执行。
- **Capabilities**（`#capabilities`）：查看标准化业务能力的领域、来源、置信度、传输信息和风险。
- **Policy**（`#policy`）：汇总各风险等级的生效动作，并编辑 `guardrails/permissions.json`。
- **Audit**（`#audit`）：筛选最近 200 条已脱敏 JSONL 运行事件；启动时传入 `--audit-log PATH` 才会记录。
- **Workflows**（`#workflows`）：展示 `analysis/agent_analysis.json` 中的多步骤业务流程；这些内容仅作为操作指导，不会自动执行。
- **Settings**（`#settings`）：汇总 kit、协议、运行时覆盖、会话记忆、适配器、登录账号管理和当前会话 token 用量。

桌面端使用持续可见的侧栏导航，760px 以下切换为完整的移动抽屉。Dry-run / 真实系统切换在所有工作区持续可见；Base URL 校验、连通测试和多账号管理仍复用同一套受 Guardrail 保护的运行时。

执行模式：

```bash
agentbridge web .agentbridge/openapi-kit \
  --base-url http://localhost:8080 \
  --bearer-env API_TOKEN \
  --execute \
  --read-only
```

也可以直接在 Console 切换运行模式。真实系统模式必须填写 `http://` 或 `https://` Base URL。连通测试先向 Base URL 发送 `HEAD`，目标不支持时回退到 `GET`；只要收到 HTTP 响应，就判定系统网络可达。切换模式会先清除待确认操作并重建运行时，原有 guardrail 和人工确认仍然生效。策略修改会写回 `guardrails/permissions.json`。Console 会把当前 Base URL 保存到 `<kit>/.agentbridge-runtime.json`，重新打开同一个 kit 时自动恢复。服务端会在终端输出必要的请求、流式事件、授权和错误日志，并对密码、token、cookie、API key 等敏感值做脱敏。

`GET /api/console` 为 Console 提供只读的 manifest、capabilities、workflows、audit、汇总信息和非敏感 settings；策略写入仍使用独立的 `POST /api/policy`。

真实执行模式下，如果开启 Save login，且登录类 HTTP/GraphQL 工具使用 username/password 参数，或返回 `access_token`、`token`、`jwt`、`Authorization` 响应头、`Set-Cookie`，Web Chat 会把对应运行时凭证保存到 `<kit>/.agentbridge-runtime.json`，并在后续工具调用中自动复用。同一个 kit 可以保存多个登录账号，页面会提供已保存账号下拉选择以及新增、修改、删除控件；普通聊天请求只提交选中账号 id，账号密码仍由本地 runtime state 读取。生成的 HTTP 工具遇到 HTTP 401 且响应显示 token expired 时，会优先用选中的已保存账号重新登录一次，再重试原工具；如果无法刷新，会明确提示用户重新选择账号或登录。该文件已加入 gitignore，不属于生成的 kit 协议文件。

终端 Chat 通过 `/use`、`/mode`、`/connect` 和 `/usage` 提供同样的核心流程。`/use` 会显示编号工具并逐项询问必填参数；需要确认的调用会给出明确的 Authorize/Cancel 选择。

启动 CLI 或 Web Chat 时可以传入 GraphQL、SQL 和 gRPC 的运行目标：

```bash
agentbridge chat .agentbridge/my-system-kit \
  --graphql-endpoint http://localhost:8080/graphql \
  --database-url sqlite:///tmp/app.db \
  --grpc-target 127.0.0.1:50051
```

允许浏览器界面切换 kit 目录：

```bash
agentbridge web .agentbridge/openapi-kit --allow-kit-switch
```

## 会话记忆

默认启用会话记忆，保存位置：

```text
<kit>/.agentbridge-chat-memory.json
```

可选参数：

```bash
agentbridge chat .agentbridge/openapi-kit --session demo --user alice
agentbridge chat .agentbridge/openapi-kit --memory-file /tmp/agentbridge-memory.json
agentbridge chat .agentbridge/openapi-kit --no-memory
```

记忆按 user/session/kit 维度保存最近聊天记录和待确认操作。

## 确认流

1. 用户请求被策略标记为 `confirm` 的操作。
2. AgentBridge 校验参数并生成 dry-run plan。
3. 待确认操作写入会话记忆。
4. CLI 或 Web UI 展示风险、method/path 和参数。
5. `confirm` 使用 `confirmed: true` 继续执行；`cancel` 清除操作。

## 运行时策略

聊天入口支持和 `serve` 相同的运行时安全参数：

```bash
agentbridge chat .agentbridge/openapi-kit --read-only
agentbridge chat .agentbridge/openapi-kit --deny-risk destructive --deny-risk external_side_effect
agentbridge chat .agentbridge/openapi-kit --allow-tool list_chapter
agentbridge chat .agentbridge/openapi-kit --audit-log .agentbridge/audit.jsonl
```

生成 Kit 的策略位于 `guardrails/permissions.json`。默认 `risk_actions` 为：

```json
{
  "read": "allow",
  "write": "confirm",
  "destructive": "deny",
  "external_side_effect": "confirm"
}
```

运行时错误会使用 `permission_denied`、`schema_mismatch`、`http_error`、`timeout`、`adapter_error` 等结构化错误码。JSONL 审计事件包含 user/session/model/tool-call 元数据，并在写入前脱敏密钥。
