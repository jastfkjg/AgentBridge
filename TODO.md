# TODO

AgentBridge 的目标是快速为现有系统搭建一个可被 Agent 操作的环境。这个环境可以是 MCP Server、SDK adapter、CLI chat 或 Web chat，核心是让用户通过自然语言安全地操作已有系统能力。

## Phase 1: OpenAPI 到可运行 MCP Server

- [x] 支持无 LLM 的确定性 kit 生成：`agentbridge generate openapi.json --output .agentbridge/openapi-kit --no-ai`
- [x] 支持 `agentbridge serve <kit>` 作为 stdio MCP Server 暴露 `tools/list` 和 `tools/call`
- [x] 支持 HTTP transport 执行：path 参数替换、GET query、JSON body、headers、Bearer token
- [x] 默认 dry-run，显式 `--execute` 才调用目标系统
- [x] 高风险工具通过 `confirmed: true` 进行人工确认
- [ ] 增加 Claude Desktop / Claude CLI / Codex MCP 配置示例
- [ ] 增加端到端示例：启动 mock HTTP API，连接 MCP client，完成一次真实读写操作

## Phase 2: 接入体验

- [ ] `agentbridge init`：交互式配置 OpenAPI 路径、base URL、认证方式、输出目录
- [ ] `agentbridge doctor`：检查 kit 完整性、目标 API 连通性、认证和高风险工具配置
- [ ] `agentbridge validate`：校验 `agentbridge-kit/v1` 协议、tools、guardrails、dry-run plan
- [ ] 生成 MCP client 配置片段，减少用户手动接线
- [x] 生成边界：不修改目标项目，所有产物写入用户指定的新目录

## Phase 3: 执行安全与治理

- [ ] per-tool allowlist / denylist
- [ ] 全局只读模式和按风险级别禁用执行
- [ ] 审计日志：记录 tool、args、user confirmation、planned call、HTTP response summary
- [ ] 高风险确认文本：要求用户明确确认对象和动作
- [ ] 请求预览：展示 method、URL、headers 摘要、body、风险理由
- [ ] 失败策略：超时、HTTP error、schema mismatch 的统一返回格式

## Phase 4: 更多 Adapter

- [ ] GraphQL adapter：query/mutation 构造、variables 映射、endpoint 配置
- [ ] 数据库 adapter：优先只读，写操作必须强确认
- [ ] OpenAI Responses API tools wrapper
- [ ] Vercel AI SDK 可执行模板，不只是 dry-run stub
- [ ] FastAPI / Express MCP Server 模板

## Phase 5: 聊天入口

- [x] CLI chat：类似 Claude CLI，通过 kit 和 MCP runtime 与系统交互
- [x] Web chat UI：可配置目标 kit、用户、会话和权限
- [x] 会话记忆与操作确认流
- [x] Human-in-the-loop UI：高风险操作在界面中展示计划调用并要求确认
- [ ] LLM planner：将自由文本意图更稳定地转换为多步工具调用
- [ ] Web UI 权限策略编辑：按用户/session 配置只读、执行、高风险确认策略

## Phase 6: 项目理解增强

- [ ] OpenAPI `$ref` 展开和更完整 JSON Schema 支持
- [ ] Python AST、TypeScript AST、Java parser 替代部分正则扫描
- [ ] Controller -> service -> repository 链路分析
- [ ] Auth middleware、permission annotation、tenant boundary 识别
- [ ] AI 分析 agent 输出更稳定的 project analysis schema

## 当前注意事项

- 当前 MCP 执行 adapter 主要覆盖 HTTP/OpenAPI transport。
- `serve` 默认 dry-run，这是安全默认值。
- 当前仓库本地 `.git/index` 曾出现 `Operation timed out`，如果影响 `git status` 或提交，需要单独修复 git 索引或重新 clone。
