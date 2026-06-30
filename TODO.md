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
- [x] 在聊天消息中以默认折叠的方式展示 curl/python 等实际命令详情
- [x] 支持 cancel/interrupt 当前 Agent 请求
- [x] 展示输入/输出 token usage 和最近 100 条 token 消耗历史
- [x] Web Chat 使用按钮处理高风险工具确认和 Claude Agent SDK 权限请求
- [x] Web Chat 在登录响应中捕获 token/cookie，并在同一用户会话后续操作中复用
- [x] Web Chat 按 kit 保存 Base URL 和多个本地运行时登录账号，并支持选择、新增、修改、删除已保存账号

## P1：更多系统 Adapter

- [x] GraphQL adapter：schema/introspection、query/mutation、variables 映射
- [x] SQL read-only adapter：只允许 SELECT、自动 LIMIT、默认 dry-run
- [x] gRPC adapter：解析 proto service/method 并生成 tools
- [x] Custom Python plugin adapter：允许用户自定义 discovery/dry-run/execute
- [x] OpenAPI auth scheme 自动映射到 runtime 配置

## P2：安全与治理

- [x] 支持 read 自动执行、write 确认、destructive 拒绝的 human-in-the-loop policy
- [x] Web UI 支持查看和编辑权限策略
- [x] 统一失败返回格式：timeout、HTTP error、schema mismatch、permission denied
- [x] 支持敏感字段脱敏策略，避免 token、cookie、password、secret 出现在 kit、日志或测试里
- [x] 为 external side effect 操作增加强制人工确认策略
- [x] 审计日志支持按用户、时间、tool、风险等级、执行结果过滤

## P3：项目理解增强

- [x] 更完整的 OpenAPI `$ref` 展开和 JSON Schema 支持
- [x] 支持 `oneOf`、`anyOf`、`nullable`、`enum`、数组 item、format、examples 等复杂 schema
- [x] Python AST 扫描与 TypeScript/Java 结构化源码扫描，减少正则误判
- [x] 分析 controller → service → repository 链路
- [x] 生成人类可读的 analysis report，说明检测到的系统、能力、风险和缺失上下文
- [x] 在分析不确定时支持交互式澄清问题，而不是直接猜测
- [x] 使用结构化输出 / JSON Schema 提升 AI 分析结果稳定性
- [x] 每个 capability 关联来源证据：文件、行号、schema path 或 OpenAPI operationId

## P4：Kit 质量与持续集成

- [x] Capability diff：比较两次生成的新增、变更、删除和风险变化
- [x] Kit migration：为未来 `agentbridge-kit/v2` 升级提供 `v1` 增量迁移基础
- [x] 重新生成时保留用户手写 prompts、skills、guardrails
- [x] 更精确生成 input schema：enum、nullable、array items、format、examples
- [x] `agentbridge generate --check` / `agentbridge diff` 支持 CI 检查
- [x] 生成更强的 tool invocation 测试，覆盖参数 schema、权限拒绝、dry-run 不执行、确认流程
- [x] 支持 kit migration 测试，保证旧版本 kit 可平滑升级

## P5：工作流与产品化

- [ ] 支持多步 workflow：Agent 自动编排多个 tools 完成排查或操作
- [ ] Workflow recording：将一次成功聊天过程保存为可复用流程
- [ ] Workflow replay：一键复用成功流程，但仍受 guardrail 控制
- [ ] Workflow 参数化：将用户输入映射到多个 tool call
- [ ] Workflow test：生成模拟输入，验证流程不会越权或执行危险操作
- [ ] Workflow approval：整个流程先 dry-run，用户确认后逐步执行
- [ ] Tool playground：每个 tool 可在 Web UI 中 dry-run / execute 测试
- [ ] System Control Console：集成工具列表、会话、审计、请求预览和确认面板
- [ ] Capability Explorer：浏览能力、来源证据、transport、schema、风险解释
- [ ] 登录账号管理支持环境维度，例如 local、staging、prod
- [ ] 请求预览支持 curl、Python、JS fetch，并显示最终 headers/body/path/query
- [ ] 会话 tool timeline 可展开查看每一步输入、输出、确认状态和失败原因
- [ ] 端到端示例：mock HTTP API + MCP client + CLI/Web Chat 完成一次真实操作

## P6：更多 Adapter 与系统集成

- [ ] PostgreSQL / MySQL read-only adapter：先只允许 SELECT，并自动 LIMIT
- [ ] Redis read-only adapter：支持 key scan、get、ttl 等只读操作
- [ ] Queue / Job adapter：支持 Celery、Sidekiq、BullMQ、RQ 等后台任务系统
- [ ] Kubernetes / Docker adapter：默认只读，写操作强确认
- [ ] Observability adapter：支持 Prometheus、Grafana、Loki、Sentry、Datadog 等观测系统
- [ ] Auth provider adapter：识别 OAuth、JWT、API key、session cookie 的运行时配置方式

## P7：AI 分析稳定性与成本控制

- [ ] 所有 AI 输出尽量使用 JSON Schema 结构化输出
- [ ] 对 AI 产物执行 normalize、validate、repair，再写入 kit
- [ ] AI 可建议风险等级变化，但最终必须通过 policy contract 校验
- [ ] 大型项目支持分层分析：架构摘要、模块分析、能力归并
- [ ] 支持缓存分析结果，减少重复读取和 token 成本
- [ ] 分析批次输出可追踪：记录输入证据、模型响应摘要、修复步骤和最终产物

## 建议优先级

短期优先：

- [ ] `agentbridge diff` / `agentbridge generate --check`
- [ ] 更完整的 OpenAPI `$ref` 和 input schema 生成
- [ ] 统一错误返回格式
- [ ] Web Tool Playground
- [ ] analysis report + evidence linking

中期重点：

- [ ] AST 扫描和调用链分析
- [ ] per-user / per-role 权限策略
- [ ] workflow recording / replay
- [ ] PostgreSQL / MySQL read-only adapter
- [ ] 重新生成时保留用户手写配置

长期方向：

- [ ] System Control Console
- [ ] 多环境治理：local / staging / prod
- [ ] 企业级审计、权限、合规脱敏
- [ ] workflow marketplace 或团队共享流程
- [ ] 多 Agent 协作分析大型系统

## 当前注意事项

- 当前真实执行 adapter 覆盖 HTTP/OpenAPI、GraphQL、SQLite read-only SQL、grpcurl gRPC 和显式标记的 Python plugin。
- `serve` 默认 dry-run，这是安全默认值。
- 写入、删除、外部副作用类操作必须保留明确的人类确认路径。
