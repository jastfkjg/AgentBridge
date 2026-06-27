# 聊天入口

AgentBridge 的聊天入口是在生成的 Agent Integration Kit 之上提供的 Claude Agent 控制界面。CLI Chat 和 Web Chat 复用同一套本地运行时：

- 从 `capabilities.json` 读取已解析系统能力
- 从 `guardrails/permissions.json` 读取安全策略
- 通过 AgentBridge runtime 调用工具
- 通过 `--execute` 选择是否真实调用 HTTP 系统
- 支持会话记忆和高风险操作确认

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

高风险操作会先暂停，并展示计划调用、风险理由、请求 URL、脱敏 headers、body 和参数。输入 `confirm` 继续，输入 `cancel` 清除待确认操作。

## Web Chat

```bash
agentbridge web .agentbridge/openapi-kit --port 8765
```

打开命令输出的 URL。Web UI 将已解析系统能力暴露为浏览器中的 Claude Agent Chat 控制入口，包含：

- 用户和会话选择
- 当前 kit 展示
- Dry-run / 真实系统运行模式切换
- Base URL 校验和目标系统连通测试
- 可点击的工具列表，自动填入 `/run` 命令和必填参数
- 聊天记录
- 高风险操作的 Authorize/Cancel 控件
- 最近一次响应和当前会话的 Claude Agent SDK Token 用量

执行模式：

```bash
agentbridge web .agentbridge/openapi-kit \
  --base-url http://localhost:8080 \
  --bearer-env API_TOKEN \
  --execute \
  --read-only
```

也可以直接在 Web 页面切换运行模式。真实系统模式必须填写 `http://` 或 `https://` Base URL。连通测试先向 Base URL 发送 `HEAD`，目标不支持时回退到 `GET`；只要收到 HTTP 响应，就判定系统网络可达。切换模式会先清除待确认操作并重建运行时，原有 guardrail 和高风险人工确认仍然生效。

终端 Chat 通过 `/use`、`/mode`、`/connect` 和 `/usage` 提供同样的核心流程。`/use` 会显示编号工具并逐项询问必填参数；高风险调用会给出明确的 Authorize/Cancel 选择。

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

记忆按 user/session/kit 维度保存最近聊天记录和待确认的高风险操作。

## 确认流

1. 用户请求高风险操作。
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
