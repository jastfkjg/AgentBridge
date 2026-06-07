# 聊天入口

AgentBridge 可以把生成的 kit 暴露成可交互聊天入口。CLI chat 和 Web chat 复用同一套本地运行时：

- 从 `capabilities.json` 读取系统能力
- 从 `guardrails/permissions.json` 读取安全策略
- 通过 AgentBridge MCP runtime 调用工具
- 通过 `--execute` 选择是否真实调用 HTTP 系统
- 支持会话记忆和高风险操作确认

## CLI Chat

```bash
agentbridge chat .agentbridge/openapi-kit
```

默认是 dry-run 模式，只返回计划调用，不触发目标系统副作用。

```bash
agentbridge chat .agentbridge/openapi-kit \
  --base-url http://localhost:8080 \
  --bearer-token "$API_TOKEN" \
  --execute \
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

高风险操作会先暂停并展示计划调用。输入 `confirm` 继续，输入 `cancel` 清除待确认操作。

## Web Chat

```bash
agentbridge web .agentbridge/openapi-kit --port 8765
```

打开命令输出的 URL。界面包含：

- 用户和会话选择
- 当前 kit 展示
- 工具列表
- 聊天记录
- 待确认操作面板

执行模式：

```bash
agentbridge web .agentbridge/openapi-kit \
  --base-url http://localhost:8080 \
  --bearer-token "$API_TOKEN" \
  --execute
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

记忆按 user/session/kit 维度保存最近聊天记录和待确认的高风险操作。

## 确认流

1. 用户请求高风险操作。
2. AgentBridge 校验参数并生成 dry-run plan。
3. 待确认操作写入会话记忆。
4. CLI 或 Web UI 展示风险、method/path 和参数。
5. `confirm` 使用 `confirmed: true` 继续执行；`cancel` 清除操作。

