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

## P8：现有问题修复与安全硬化

- [ ] 确认等待消息、pending state、Python plugin preview、gRPC request message 等所有用户可见预览统一复用递归脱敏，避免 password、token、cookie、secret 在确认界面中明文出现
- [ ] 补强 runtime 参数校验：区分 integer 与 number，校验 enum、nullable、array items、nested object、format，并与生成的 input schema 保持一致
- [ ] Web policy 编辑器保存前执行 schema/policy contract 校验，拒绝未知 action、非法 risk、损坏 JSON 结构，并给出可操作错误
- [ ] Web policy 编辑器增加变更 diff 和风险提示，尤其是 deny -> confirm/allow、confirm -> allow 的降级操作
- [ ] 为 `guardrails/permissions.json`、chat memory、runtime state 等写入路径增加原子写入或文件锁，减少多会话并发覆盖
- [ ] 为 generated files 增加生成标记或内容 hash，重新生成时只保留真实用户改动，避免把未改动的旧生成文件误判为用户手写内容
- [ ] 生成的 `tests/test_generated_tools.py` 应能在 kit 目录独立运行，不依赖开发 checkout 或本地 `PYTHONPATH=src`
- [ ] 统一 CLI、Web Chat、MCP 的错误展示：structured error 中的 code、category、detail、next_step 都应被稳定透出
- [ ] `agentbridge generate --check` 和 `agentbridge diff` 输出保持确定性排序，并支持机器可读 JSON 摘要供 CI 注释使用
- [ ] 扩展 secret regression tests，覆盖请求预览、确认消息、plugin preview、错误对象、审计日志和生成产物

## P9：性能与可扩展性优化

- [ ] Discovery 支持按文件 hash 增量扫描，只重扫变更过的 OpenAPI、GraphQL、SQL、源码和配置文件
- [ ] 扫描器支持并行执行，但输出顺序、capability 命名和 diff 结果必须保持确定性
- [ ] 大型项目生成时输出阶段性进度、耗时、扫描文件数、候选 capability 数和 AI token 成本估算
- [ ] 为 capability、evidence、schema 建立索引，提升大型 kit 的搜索、过滤和 Web 渲染速度
- [ ] 重新生成时跳过内容未变化的文件写入，减少无意义 git diff 和 CI 噪音
- [ ] 分析缓存支持按 evidence hash 失效，避免小改动触发全量 AI 重新分析
- [ ] 增加 `agentbridge profile` 或 benchmark 命令，用 fixture 衡量 discovery、generation、validate、diff 的耗时和输出规模
- [ ] 对超大 OpenAPI/GraphQL schema 做分块摘要和懒加载，避免一次性塞入 Agent 上下文

## P10：可新增功能

- [ ] `agentbridge init` 向导：自动检测项目类型、推荐 adapter、生成初始 kit、运行 validate，并给出下一步命令
- [ ] `agentbridge doctor` 扩展为交互式修复：缺少 `grpcurl`、Base URL 不可达、policy 损坏、kit 文件缺失时给出一键修复建议
- [ ] Tool Playground 支持保存请求样例、复制 curl/Python/JS、对比 dry-run 与 execute 结果
- [ ] Capability Explorer 支持按风险、transport、resource、action、confidence、来源文件过滤
- [ ] Audit Log Viewer 支持 Web 查询、导出 JSONL/CSV、按用户/session/tool/risk/outcome/time 过滤
- [ ] Workflow recording/replay 支持把一次成功对话固化为可审计流程，并在 replay 前展示完整 dry-run plan
- [ ] 多环境配置：local、staging、prod 各自保存 Base URL、认证方式、登录账号、风险策略和审计路径
- [ ] Adapter SDK：为 Python plugin adapter 提供模板、类型定义、测试 harness 和示例项目
- [ ] 支持 PostgreSQL/MySQL read-only adapter，并为常见 ORM schema、migration 文件生成只读能力
- [ ] 支持 Observability adapter，把日志、指标、trace、错误系统暴露成默认只读排查工具

## P11：用户体验与产品化

- [ ] Web 首页从 chat-only 升级为 System Control Console，整合 Chat、Tools、Capabilities、Policy、Audit、Workflows、Settings
- [ ] Policy UI 从 JSON textarea 升级为表单化编辑：risk action 使用分段控件，tool override 使用表格和批量操作
- [ ] 高风险确认 UI 展示风险原因、目标环境、最终 URL、headers/body 摘要、变更对象和确认人信息
- [ ] Chat tool timeline 支持按步骤展开输入、输出、请求预览、错误、确认状态和耗时
- [ ] 空状态和错误状态补齐：没有 AI backend、没有 adapter、没有 base URL、没有登录账号时给出明确下一步
- [ ] 生成完成页展示 kit 健康状态、capability 数量、风险分布、缺失上下文和推荐验证命令
- [ ] 登录账号管理增加环境隔离、最后使用时间、认证过期提示和手动刷新按钮
- [ ] CLI 输出增加 `--json` 和更清晰的 human-readable summary，方便脚本和人工使用
- [ ] 文档补一条完整 happy path：从现有 mock 服务生成 kit，到 Web Chat 登录、dry-run、确认执行、审计回看
- [ ] 提供示例视频脚本或 demo checklist，方便后续做 README/GIF/演示页面

## 建议优先级

短期优先：

- [ ] 修复确认消息和所有 request preview 的敏感字段脱敏缺口
- [ ] 补强 runtime 参数校验，使执行前校验真正匹配生成的 JSON Schema
- [ ] Web policy 保存前校验、风险 diff、非法策略拒绝
- [ ] 生成文件 preservation 增加生成标记/hash，避免误保留旧生成内容
- [ ] `generate --check` / `diff` 输出确定性 JSON 摘要，便于 CI 使用
- [ ] 增加端到端示例：mock HTTP API + MCP client + CLI/Web Chat + 审计回看

中期重点：

- [ ] Web Tool Playground 和 Capability Explorer
- [ ] workflow recording / replay
- [ ] PostgreSQL / MySQL read-only adapter
- [ ] 多环境配置和登录账号环境隔离
- [ ] 增量 discovery cache 和大型项目分层分析
- [ ] Audit Log Viewer 和 policy 表单化编辑

长期方向：

- [ ] System Control Console
- [ ] 多环境治理与 per-user / per-role 权限策略
- [ ] 企业级审计、权限、合规脱敏和审批链
- [ ] workflow marketplace 或团队共享流程
- [ ] 多 Agent 协作分析大型系统
- [ ] Adapter SDK 与第三方 adapter 生态

## 当前注意事项

- 当前真实执行 adapter 覆盖 HTTP/OpenAPI、GraphQL、SQLite read-only SQL、grpcurl gRPC 和显式标记的 Python plugin。
- `serve` 默认 dry-run，这是安全默认值。
- 写入、删除、外部副作用类操作必须保留明确的人类确认路径。
- P2、P3、P4 已完成，下一阶段不要再把这些能力作为短期 TODO 重复排期，除非是上面 P8 中列出的硬化或缺陷修复。
- `agentbridge-kit/v1` 必需路径仍需保持稳定；新增文件可以 additive 方式加入，删除或重命名必需文件需要 bump `KIT_PROTOCOL_VERSION`。
- 用户提供的 API key、cookie、token 只能作为运行时配置使用，不能写入 kit、测试、示例、审计明文或 TODO 复现步骤。
