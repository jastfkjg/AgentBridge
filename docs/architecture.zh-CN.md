# 架构

AgentBridge 采用 AI agent 优先的生成流水线。

## 流程

1. 候选发现器扫描 schema、路由和数据库定义。
2. AI 分析 agent 读取项目代码和候选证据。
3. AI agent 产出项目分析、风险推理、增强能力、skills 和 prompts。
4. 生成器写入 `agentbridge-kit/v1` 协议目录。
5. 运行时工具在执行宿主系统 adapter 前，先执行 guardrails 和 dry-run 校验。

## 为什么仍然保留规则

规则适合廉价、确定性地收集证据，但不应该被当成最终业务模型。真正理解 controller/service 行为、工作流意图、副作用和代码隐含操作的是 AI 分析层。

## 安全边界

生成阶段可以推断工具，但运行时执行必须服从 `guardrails/permissions.json`。生成的助手不能在没有明确人工确认的情况下执行破坏性或外部副作用操作。

