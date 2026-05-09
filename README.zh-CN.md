# AgentBridge

**[English](README.md)**

**将现有系统秒变 AI Agent 就绪平台。**

AgentBridge 从 API Schema、数据库 Schema 和源码路由中自动发现系统能力，然后生成完整的 **Agent 集成套件**，开箱即用支持 MCP、Claude、OpenAI 和 Vercel AI SDK。

内置 **Claude Agent SDK** 支持和 **AI 动态生成**能力，AgentBridge 超越静态模板 — 它使用 AI 为你的系统量身定制更丰富的工具定义、领域技能和智能提示词。

---

## ✨ 特性

- **自动发现** — 从 OpenAPI、GraphQL、SQL 和源码路由（Python/Flask/FastAPI、JS/Express、Java/Spring）中提取能力
- **多格式工具生成** — 同时输出 MCP、Claude、OpenAI 和 Vercel AI SDK 工具定义
- **AI 驱动生成** — 使用 Claude Agent SDK（首选）或 Anthropic API（备选）动态生成更丰富的工具、技能和提示词，而非依赖硬编码模板
- **Agent 即服务** — 使用 Claude Agent SDK 作为已有项目的交互式 Agent，结合生成的工具和技能
- **安全优先** — 按风险等级（`read` / `write` / `destructive` / `external_side_effect`）分类操作，危险操作需人工确认
- **Dry-run 验证** — 在执行前根据生成的护栏测试工具调用
- **轻依赖核心** — 确定性生成器仅依赖 Python 标准库；AI 功能需要 API Key

---

## 🚀 快速开始

### 安装

```bash
pip install agentbridge
```

AI 驱动生成和 Agent 会话（推荐）：

```bash
pip install agentbridge[agent]
```

轻量级 AI 生成（不含 Claude Agent SDK）：

```bash
pip install agentbridge[ai]
```

全部安装：

```bash
pip install agentbridge[all]
```

### 配置 LLM 提供商

AgentBridge 的 AI 功能需要 LLM API Key。默认使用 Anthropic Claude，但你可以配置任何兼容 Anthropic 协议的提供商（DeepSeek、OpenRouter 等）：

```bash
# 必需：API Key
export ANTHROPIC_API_KEY="sk-ant-..."

# 可选：自定义 API 端点（用于 DeepSeek、OpenRouter 等）
# DeepSeek Anthropic 端点：
export ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"
# OpenRouter：
export ANTHROPIC_BASE_URL="https://openrouter.ai/api/v1"

# 可选：自定义模型名称
export ANTHROPIC_MODEL="deepseek-v4-flash"
```

或通过 CLI 参数传入：

```bash
agentbridge generate examples/writing_system --output build/kit --ai \
  --api-key "sk-..." \
  --base-url "https://api.deepseek.com/anthropic" \
  --model "deepseek-v4-flash"
```

> **注意：** 设置 `ANTHROPIC_BASE_URL` 后，AgentBridge 自动使用 `anthropic` SDK 后端（而非 `claude-agent-sdk`）进行生成，因为自定义端点不被 Agent SDK 支持。

### 生成 Agent 集成套件

```bash
# 确定性生成（无需 API Key）
agentbridge generate examples/writing_system --output .agentbridge/writing-kit

# AI 驱动生成（需要 ANTHROPIC_API_KEY）
agentbridge generate examples/writing_system --output .agentbridge/writing-kit --ai
```

### 作为 Agent 运行

```bash
# 为你的项目启动交互式 AI Agent 会话
agentbridge chat .agentbridge/writing-kit
```

### 查看生成文件

```bash
find .agentbridge/writing-kit -maxdepth 3 -type f | sort
```

### 运行测试

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

---

## 📖 CLI 参考

### 发现能力

以 JSON 格式打印所有发现的能力：

```bash
agentbridge discover examples/writing_system
```

### 生成 Agent 集成套件

```bash
# 确定性
agentbridge generate examples/writing_system --output build/agent-kit

# AI 驱动（需要 ANTHROPIC_API_KEY）
agentbridge generate examples/writing_system --output build/agent-kit --ai

# 自定义名称
agentbridge generate examples/writing_system --output build/agent-kit --name my-kit
```

### Dry-run 工具调用

```bash
agentbridge dry-run build/agent-kit create_chapter --args '{"project_id":"p1","title":"Opening"}'
```

高风险操作需人工确认：

```bash
agentbridge dry-run build/agent-kit delete_character --args '{"project_id":"p1","character_id":"c1"}' --confirmed
```

### AI Agent 对话

启动基于 Claude Agent SDK 的交互式 AI Agent 会话：

```bash
agentbridge chat build/agent-kit
```

---

## 🔍 AgentBridge 能发现什么

| 来源类型 | 格式 |
|---|---|
| API Schema | OpenAPI JSON/YAML、GraphQL Schema |
| 数据库 Schema | SQL `CREATE TABLE` 语句 |
| Python 路由 | FastAPI `@router.get/post/...`、Flask `@app.route` |
| JavaScript 路由 | Express `app.get/post/...` |
| Java 路由 | Spring `@GetMapping/@PostMapping/...` |

所有来源被标准化为统一的能力模型，包含：

- **domain** 和 **resource** — 逻辑分组
- **action** 动词 — 能力做什么
- **input schema** — JSON Schema 参数
- **risk level** — `read`、`write`、`destructive` 或 `external_side_effect`
- **确认要求** — 是否需要人工审批
- **来源追踪** — 完整可追溯至原始文件

---

## 📁 生成套件结构

```text
agent-kit/
  manifest.json              # 套件元数据和摘要
  capabilities.json          # 所有发现的能力
  tools/
    mcp_tools.json           # MCP 工具定义
    openai_tools.json        # OpenAI 函数调用格式
    claude_tools.json        # Claude 工具使用格式
    vercel_ai_tools.ts       # Vercel AI SDK TypeScript 工具
  skills/
    writing.md               # 领域特定技能定义
  prompts/
    system.md                # Agent 系统提示词
  resources/
    schema.json              # 资源 Schema 摘要
  guardrails/
    permissions.json         # 风险策略和确认规则
  tests/
    tool_invocation_tests.json   # 自动生成的调用测试
    test_generated_tools.py      # Python 工具契约单元测试
  dry_run_plan.json          # Dry-run 执行计划
```

---

## 🤖 AI 驱动生成

使用 `--ai` 时，AgentBridge 使用 Claude 动态生成：

- **增强的工具描述** — 上下文感知的描述，捕捉业务语义而非仅 HTTP 方法 + 路径
- **领域特定技能** — 为你的领域定制的技能工作流提示词，包含最佳实践和边界情况处理
- **智能系统提示词** — 理解资源间关系的提示词，建议安全操作序列
- **推断的额外工具** — Schema 隐含但未显式存在的工具（如搜索、批量操作）
- **改进的风险评估** — 超越关键词匹配的上下文感知风险分类

### AI 后端

AgentBridge 支持两种 AI 后端，运行时自动检测：

| 后端 | 包 | 用途 |
|---|---|---|
| **Claude Agent SDK**（首选） | `claude-agent-sdk` | 生成 + 交互式 Agent 会话 |
| **Anthropic API**（备选） | `anthropic` | 仅生成，支持自定义端点 |

安装了 `claude-agent-sdk` 且未设置自定义 `ANTHROPIC_BASE_URL` 时，自动用于生成和 Agent 会话。配置了自定义端点或未安装 `claude-agent-sdk` 时，使用 `anthropic` SDK 进行生成。

### 配置

AI 功能**必须**配置 LLM API Key。通过环境变量配置：

```bash
export ANTHROPIC_API_KEY="sk-ant-..."          # 必需
export ANTHROPIC_BASE_URL=""                    # 可选：自定义端点
export ANTHROPIC_MODEL="claude-sonnet-4-20250514"  # 可选：模型名称
```

或编程式传入：

```python
from agentbridge.agent import AIGenerator

# 默认：Anthropic Claude
ai = AIGenerator(api_key="sk-ant-...")

# 自定义提供商（如 DeepSeek）
ai = AIGenerator(
    api_key="sk-d831ecabc21842fdae6f30c24dd3b052",
    base_url="https://api.deepseek.com/anthropic",
    model="deepseek-v4-flash",
)

# 自定义提供商（如 OpenRouter）
ai = AIGenerator(
    api_key="sk-or-...",
    base_url="https://openrouter.ai/api/v1",
    model="anthropic/claude-sonnet-4-20250514",
)
```

### 编程式使用

```python
from pathlib import Path
from agentbridge.generator import AgentKitGenerator
from agentbridge.agent import AIGenerator

# 确定性生成（无需 API Key）
kit = AgentKitGenerator().generate(
    [Path("examples/writing_system")],
    Path("build/agent-kit"),
)

# AI 驱动生成（需要 API Key）
ai = AIGenerator(api_key="sk-ant-...")
kit = AgentKitGenerator(ai_generator=ai).generate(
    [Path("examples/writing_system")],
    Path("build/agent-kit"),
)
```

### Claude Agent SDK 集成

将 AgentBridge 作为已有项目的交互式 Agent 使用：

```python
import asyncio
from agentbridge.agent import AgentRunner

async def main():
    runner = AgentRunner(kit_dir="build/agent-kit", api_key="sk-ant-...")
    async for message in runner.query("列出项目 p1 的所有章节"):
        print(message)

asyncio.run(main())
```

`AgentRunner` 底层使用 `ClaudeSDKClient`，将生成的工具注册为 in-process MCP 服务器。这意味着 Claude 可以在对话中直接调用你项目的功能作为工具。

---

## 🛡️ 安全模型

AgentBridge 根据方法名、HTTP 动词、路由名和副作用关键词对操作进行分类：

| 风险等级 | 是否需要确认 | 示例 |
|---|---|---|
| `read` | 不需要 | GET、list、search、find |
| `write` | 按策略可选 | POST、create、update、rewrite |
| `destructive` | **必须** | DELETE、remove、destroy、drop、cancel |
| `external_side_effect` | **必须** | publish、send、email、pay、deploy、export |

安全模型在确定性和 AI 生成的输出中一致应用。AI 生成的工具始终经过相同的护栏验证。

---

## 🏗️ 架构

```text
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│  发现层      │────▶│  生成器       │────▶│  Agent 套件     │
│  (Schema,   │     │  (确定性 +   │     │  (工具、技能、  │
│   路由,     │     │   AI 驱动)   │     │   提示词、      │
│   SQL)      │     │              │     │   护栏)         │
└─────────────┘     └──────┬───────┘     └────────┬────────┘
                           │                      │
                    ┌──────▼───────┐       ┌──────▼────────┐
                    │  AI 生成器   │       │  Agent 运行器  │
                    │  (Claude SDK │       │  (Claude SDK  │
                    │   / Anthropic│       │   Client +    │
                    │   API)       │       │   MCP 工具)   │
                    └──────────────┘       └───────────────┘
```

---

## 🧩 扩展 AgentBridge

- **添加新的 Schema 解析器** — 在 `discovery.py` 中按现有模式实现发现器
- **添加新的工具格式** — 在 `generator.py` 中添加构建函数
- **自定义 AI 提示词** — 在 `agent.py` 中覆盖默认的 AI 生成提示词
- **自定义风险策略** — 修改 `policy.py` 以更改风险等级的分类方式
- **自定义 Agent 工具** — 扩展 `AgentRunner._build_kit_tools()` 添加自定义工具处理器

---

## 📋 系统要求

- Python 3.10+
- **AI 生成（`--ai` 必需）：** `claude-agent-sdk`（推荐）或 `anthropic` + `ANTHROPIC_API_KEY`
- **Agent 会话（`chat` 必需）：** `claude-agent-sdk` + `ANTHROPIC_API_KEY`

---

## 📄 许可证

MIT 许可证 — 详见 [LICENSE](LICENSE)。
