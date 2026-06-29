# TODO

AgentBridge 的目标：通过 Claude Agent SDK 解析已有项目或系统，生成可被 Claude Agent Chat 控制的版本化工具层，让用户通过 CLI/Web Chat 以自然语言理解、查询和安全控制已有系统。

## 已完成基础能力

- [x] 确定性扫描并生成 `agentbridge-kit/v1`
- [x] 支持 Claude Agent SDK / Anthropic API 做项目语义分析
- [x] 生成 MCP、Claude tools、OpenAI tools、Vercel AI SDK 等集成产物
- [x] 提供 stdio MCP Server，支持 `tools/list` 和 `tools/call`
- [x] 支持 HTTP/OpenAPI transport 执行：path、query、JSON body、headers、Bearer token
- [x] 默认 dry-run，显式 `--execute` 才调用目标系统
- [x] 支持 per-tool allow/deny、只读模式、风险级别禁用
- [x] 支持高风险操作人工确认和请求预览
- [x] 支持 CLI chat 与 Web chat，通过 Agent 与生成的 kit 交互
- [x] 支持审计日志、MCP client 配置生成、kit validate/doctor

## P0：Agent Chat 体验

- [x] 持久化 Claude Agent SDK client/session，支持多轮 resume
- [x] Web Chat 支持 SSE/WebSocket 流式响应
- [x] 在 UI 中实时展示 tool use、tool result、确认等待和执行结果
- [x] 增加 tool call timeline，方便用户理解 Agent 如何操作系统
- [x] 支持 cancel/interrupt 当前 Agent 请求
- [x] 展示 model、token usage、cost 等运行信息
- [x] Web Chat 使用按钮处理高风险工具确认和 Claude Agent SDK 权限请求
- [x] Web Chat 在登录响应中捕获 token/cookie，并在同一用户会话后续操作中复用
- [x] Web Chat 按 kit 保存 Base URL 和本地运行时登录参数

## P1：更多系统 Adapter

- [x] GraphQL adapter：schema/introspection、query/mutation、variables 映射
- [x] SQL read-only adapter：只允许 SELECT、自动 LIMIT、默认 dry-run
- [x] gRPC adapter：解析 proto service/method 并生成 tools
- [x] Custom Python plugin adapter：允许用户自定义 discovery/dry-run/execute
- [x] OpenAPI auth scheme 自动映射到 runtime 配置

## P2：安全与治理

- [ ] 强确认机制：高风险操作要求输入 tool name、对象 ID 或确认短语
- [ ] per-user / per-role / per-environment 权限策略
- [ ] 支持 read 自动执行、write 确认、destructive 拒绝的 human-in-the-loop policy
- [ ] 审计日志增强：session id、user、model、tool call id、确认来源、脱敏策略
- [ ] Web UI 支持查看和编辑权限策略
- [ ] 统一失败返回格式：timeout、HTTP error、schema mismatch、permission denied

## P3：项目理解增强

- [ ] 更完整的 OpenAPI `$ref` 展开和 JSON Schema 支持
- [ ] Python/TypeScript/Java AST 扫描，减少正则误判
- [ ] 分析 controller → service → repository 链路
- [ ] 识别 auth middleware、permission annotation、tenant boundary
- [ ] 生成人类可读的 analysis report，说明检测到的系统、能力、风险和缺失上下文
- [ ] 在分析不确定时支持交互式澄清问题，而不是直接猜测
- [ ] 使用结构化输出 / JSON Schema 提升 AI 分析结果稳定性

## P4：Kit 质量与持续集成

- [ ] Capability diff：比较两次生成的新增、变更、删除和风险变化
- [ ] Kit migration：支持未来 `agentbridge-kit/v2` 升级
- [ ] 重新生成时保留用户手写 prompts、skills、guardrails
- [ ] 更精确生成 input schema：enum、nullable、array items、format、examples
- [ ] Vercel AI SDK 从 JSON Schema 生成精确 Zod schema
- [ ] `agentbridge generate --check` / `agentbridge diff` 支持 CI 检查

## P5：工作流与产品化

- [ ] 支持多步 workflow：Agent 自动编排多个 tools 完成排查或操作
- [ ] Workflow recording：将一次成功聊天过程保存为可复用流程
- [ ] Tool playground：每个 tool 可在 Web UI 中 dry-run / execute 测试
- [ ] System Control Console：集成工具列表、会话、审计、请求预览和确认面板
- [ ] 端到端示例：mock HTTP API + MCP client + CLI/Web Chat 完成一次真实操作

## 当前注意事项

- 当前真实执行 adapter 覆盖 HTTP/OpenAPI、GraphQL、SQLite read-only SQL、grpcurl gRPC 和显式标记的 Python plugin。
- `serve` 默认 dry-run，这是安全默认值。
- 写入、删除、外部副作用类操作必须保留明确的人类确认路径。
