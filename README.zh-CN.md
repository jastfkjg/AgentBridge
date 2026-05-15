<div align="center">

# 🌉 AgentBridge

**将现有系统秒变 AI Agent 就绪平台。**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![PRs Welcome](https://img.shields.io/badge/PRs-Welcome-brightgreen.svg)](CONTRIBUTING.md)

[English](README.md) · [中文](README.zh-CN.md)

</div>

---

AgentBridge 使用 AI 分析 agent 理解项目代码，然后生成完整的 **Agent 集成套件**，开箱即用支持 MCP、Claude、OpenAI 和 Vercel AI SDK。

确定性扫描器仍会从 OpenAPI、GraphQL、SQL 和源码路由收集候选证据，但它们不是最终事实来源。AI agent 会读取项目上下文，推理业务对象、副作用和权限边界，再生成 tools、skills、prompts、guardrails、tests 和协议元数据。

## 📑 目录

- [✨ 特性](#-特性)
- [🚀 快速开始](#-快速开始)
- [📖 CLI 参考](#-cli-参考)
- [🔍 AgentBridge 如何分析项目](#-agentbridge-如何分析项目)
- [🔍 候选证据来源](#-候选证据来源)
- [📁 稳定套件协议](#-稳定套件协议)
- [🤖 AI Agent 生成](#-ai-agent-生成)
- [🛡️ 安全模型](#-安全模型)
- [🏗️ 架构](#-架构)
- [📚 文档](#-文档)
- [🧩 扩展 AgentBridge](#-扩展-agentbridge)
- [📦 发布与安装](#-发布与安装)
- [🤝 参与贡献](#-参与贡献)
- [📄 许可证](#-许可证)

---

## ✨ 特性

<table>
<tr><td width="50%">

🔍 **AI 优先代码分析**
由 AI agent 理解项目中的业务对象、工作流、权限边界和副作用

</td><td width="50%">

🔧 **多格式生成**
同时输出 MCP、Claude、OpenAI 和 Vercel AI SDK 工具定义

</td></tr>
<tr><td>

🤖 **AI 驱动生成**
使用 Claude Agent SDK（首选）或 Anthropic API（备选）动态生成工具、技能和提示词

</td><td>

🧠 **Agent 即服务**
通过 Claude Agent SDK 作为已有项目的交互式 Agent 运行

</td></tr>
<tr><td>

🛡️ **安全优先**
按风险等级分类操作，危险操作需人工确认

</td><td>

🧪 **Dry-run 验证**
在执行前根据生成的护栏测试工具调用

</td></tr>
<tr><td>

🌐 **自定义 LLM 提供商**
支持 DeepSeek、OpenRouter 等任何兼容 Anthropic 协议的端点

</td><td>

🪶 **规则作为证据**
OpenAPI、GraphQL、SQL 和路由扫描器只提供候选信号，由 AI agent 验证或覆盖

</td></tr>
</table>

---

## 🚀 快速开始

### 安装

```bash
pip install agbr
```

<details>
<summary>📦 安装可选功能</summary>

```bash
# AI 驱动生成 + Agent 会话（推荐）
pip install "agbr[agent]"

# 轻量级 AI 生成（不含 Claude Agent SDK）
pip install "agbr[ai]"

# 全部安装
pip install "agbr[all]"

# 从源码安装（开发用）
git clone git@github.com:jastfkjg/AgentBridge.git
cd AgentBridge
pip install -e ".[all]"
```

</details>

### 配置 LLM 提供商

AgentBridge 的所有生成功能需要 LLM API Key。默认使用 Anthropic Claude，但你可以配置任何兼容 Anthropic 协议的提供商：

```bash
# 必需
export ANTHROPIC_API_KEY="sk-ant-..."

# 可选：自定义 API 端点
export ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"   # DeepSeek
# export ANTHROPIC_BASE_URL="https://openrouter.ai/api/v1"       # OpenRouter

# 可选：自定义模型名称
export ANTHROPIC_MODEL="deepseek-v4-flash"
```

<details>
<summary>🔑 或通过 CLI 参数传入</summary>

```bash
agentbridge generate examples/writing_system --output build/kit \
  --api-key "sk-..." \
  --base-url "https://api.deepseek.com/anthropic" \
  --model "deepseek-v4-flash"
```

> **注意：** 设置 `ANTHROPIC_BASE_URL` 后，AgentBridge 自动使用 `anthropic` SDK 后端（而非 `claude-agent-sdk`）进行生成，因为自定义端点不被 Agent SDK 支持。

</details>

### 生成 Agent 集成套件

```bash
# 需要 ANTHROPIC_API_KEY（通过环境变量或 --api-key 参数设置）
agentbridge generate examples/writing_system --output .agentbridge/writing-kit
```

### 作为 Agent 运行

```bash
agentbridge chat .agentbridge/writing-kit
```

### 运行测试

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

---

## 📖 CLI 参考

| 命令 | 说明 |
|---|---|
| `agentbridge discover <paths>` | 发现并打印能力为 JSON |
| `agentbridge generate <paths> -o <dir>` | 用 AI 分析代码并生成 Agent 集成套件 |
| `agentbridge dry-run <kit> <tool>` | Dry-run 工具调用 |
| `agentbridge chat <kit>` | 启动交互式 AI Agent 会话 |

<details>
<summary>📝 完整命令详情</summary>

### `discover`

```bash
agentbridge discover examples/writing_system
```

### `generate`

```bash
agentbridge generate examples/writing_system --output build/agent-kit

# 自定义名称
agentbridge generate examples/writing_system --output build/agent-kit --name my-kit

# 自定义 LLM 提供商
agentbridge generate examples/writing_system --output build/agent-kit \
  --api-key "sk-..." --base-url "https://api.deepseek.com/anthropic" --model "deepseek-v4-flash"
```

### `dry-run`

```bash
# 普通调用
agentbridge dry-run build/agent-kit create_chapter --args '{"project_id":"p1","title":"Opening"}'

# 高风险操作（需要确认）
agentbridge dry-run build/agent-kit delete_character \
  --args '{"project_id":"p1","character_id":"c1"}' --confirmed
```

### `chat`

```bash
agentbridge chat build/agent-kit
# 或使用自定义 LLM 提供商：
agentbridge chat build/agent-kit --api-key "sk-..." --base-url "https://api.deepseek.com/anthropic"
```

</details>

---

## 🔍 AgentBridge 如何分析项目

AgentBridge 的设计目标是让 AI agent 承担主要项目理解工作。规则发现刻意保持保守，只负责收集证据。

| 阶段 | 作用 |
|---|---|
| 候选扫描 | 提取 OpenAPI 操作、GraphQL 字段、SQL 表和路由处理器 |
| AI 项目分析 | 推断业务对象、工作流、权限边界、副作用、缺失操作和假设 |
| 能力标准化 | 将 AI 增强后的分析转换为稳定的工具能力 |
| 套件生成 | 输出 tools、skills、prompts、resource schemas、guardrails、dry-run plans 和 tests |

生成套件会保留两层信息：

- `analysis/rule_signals.json`：确定性扫描器的候选证据
- `analysis/agent_analysis.json`：AI agent 的项目分析和推理

## 🔍 候选证据来源

| 来源类型 | 格式 |
|---|---|
| 🌐 API Schema | OpenAPI JSON/YAML、GraphQL Schema |
| 🗄️ 数据库 Schema | SQL `CREATE TABLE` 语句 |
| 🐍 Python 路由 | FastAPI `@router.get/post/...`、Flask `@app.route` |
| 📜 JavaScript 路由 | Express `app.get/post/...` |
| ☕ Java 路由 | Spring `@GetMapping/@PostMapping/...` |

所有来源被标准化为统一的能力模型，包含：

| 字段 | 说明 |
|---|---|
| `domain` + `resource` | 逻辑分组 |
| `action` | 能力做什么 |
| `input_schema` | JSON Schema 参数 |
| `risk` | `read` / `write` / `destructive` / `external_side_effect` |
| `confirm_required` | 是否需要人工审批 |
| `source` | 完整可追溯至原始文件 |

---

## 📁 稳定套件协议

当前协议：`agentbridge-kit/v1`。详见 [docs/kit-protocol.zh-CN.md](docs/kit-protocol.zh-CN.md)。

```text
agent-kit/
├── manifest.json                  # 套件元数据和摘要
├── capabilities.json              # AI 增强后的标准能力
├── analysis/
│   ├── rule_signals.json          # 提供给 AI 的扫描证据
│   └── agent_analysis.json        # AI 项目分析和风险推理
├── spec/
│   └── kit-protocol.md            # 复制进套件的协议契约
├── tools/
│   ├── mcp_tools.json             # MCP 工具定义
│   ├── openai_tools.json          # OpenAI 函数调用格式
│   ├── claude_tools.json          # Claude 工具使用格式
│   └── vercel_ai_tools.ts         # Vercel AI SDK TypeScript 工具
├── skills/
│   └── writing.md                 # 领域特定技能定义
├── prompts/
│   └── system.md                  # Agent 系统提示词
├── resources/
│   └── schema.json                # 资源 Schema 摘要
├── guardrails/
│   └── permissions.json           # 风险策略和确认规则
├── tests/
│   ├── tool_invocation_tests.json # 自动生成的调用测试
│   └── test_generated_tools.py    # Python 工具契约单元测试
└── dry_run_plan.json              # Dry-run 执行计划
```

---

## 🤖 AI Agent 生成

AgentBridge 使用 AI 分析 agent 生成套件中的语义内容：项目分析、工具描述、skills、系统提示词、风险评估和推断工具。规则分析会作为候选证据和安全提示传给 agent，而不是直接复制为最终输出。

| 生成内容 | 说明 |
|---|---|
| 🧭 **项目分析** | 业务对象、工作流、权限边界、副作用和假设 |
| 📝 **增强的工具描述** | 上下文感知的描述，捕捉业务语义 |
| 🎯 **领域特定技能** | 为你的领域定制的技能工作流提示词 |
| 🧠 **智能系统提示词** | 理解资源间关系的提示词，建议安全操作序列 |
| 🔍 **推断的额外工具** | Schema 隐含但未显式存在的工具 |
| ⚠️ **改进的风险评估** | LLM 结合规则提示评估风险 |

### AI 后端

| 后端 | 包 | 用途 |
|---|---|---|
| **Claude Agent SDK**（首选） | `claude-agent-sdk` | 生成 + 交互式 Agent 会话 |
| **Anthropic API**（备选） | `anthropic` | 仅生成，支持自定义端点 |

安装了 `claude-agent-sdk` 且未设置自定义 `ANTHROPIC_BASE_URL` 时，自动用于生成和 Agent 会话。配置了自定义端点或未安装 `claude-agent-sdk` 时，使用 `anthropic` SDK 进行生成。

### 编程式使用

```python
from pathlib import Path
from agentbridge.generator import AgentKitGenerator
from agentbridge.agent import AIGenerator, AgentRunner

# 使用默认提供商（Anthropic Claude）生成
ai = AIGenerator(api_key="sk-ant-...")
kit = AgentKitGenerator(ai_generator=ai).generate(
    [Path("examples/writing_system")],
    Path("build/agent-kit"),
)

# 自定义 LLM 提供商（如 DeepSeek）
ai = AIGenerator(
    api_key="sk-d831ecabc21842fdae6f30c24dd3b052",
    base_url="https://api.deepseek.com/anthropic",
    model="deepseek-v4-flash",
)

# Agent 会话
import asyncio
async def main():
    runner = AgentRunner(kit_dir="build/agent-kit", api_key="sk-ant-...")
    async for message in runner.query("列出项目 p1 的所有章节"):
        print(message)
asyncio.run(main())
```

---

## 🛡️ 安全模型

| 风险等级 | 是否需要确认 | 示例 |
|---|---|---|
| 🟢 `read` | 不需要 | GET、list、search、find |
| 🟡 `write` | 按策略可选 | POST、create、update、rewrite |
| 🔴 `destructive` | **必须** | DELETE、remove、destroy、drop、cancel |
| 🟠 `external_side_effect` | **必须** | publish、send、email、pay、deploy、export |

安全模型一致应用。基于规则的风险分类提供初始上下文，LLM 可在有充分理由时覆盖。

---

## 🏗️ 架构

```text
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│ 规则信号     │────▶│ AI 分析 Agent│────▶│ 套件生成器      │
│ (Schema,    │     │             │     │ (protocol v1)  │
│  路由, SQL) │     │             │     │                │
└─────────────┘     └──────┬──────┘     └────────┬────────┘
                           │                     │
                    ┌──────▼──────┐       ┌──────▼────────┐
                    │ 能力/Skills │       │ Agent 运行时  │
                    │ Guardrails  │       │ Dry-run +     │
                    │             │       │ Guardrails    │
                    └─────────────┘       └───────────────┘
```

---

## 📚 文档

- [Architecture](docs/architecture.md)
- [Kit protocol](docs/kit-protocol.md)
- [English README](README.md)
- [中文架构说明](docs/architecture.zh-CN.md)
- [中文套件协议](docs/kit-protocol.zh-CN.md)

---

## 🧩 扩展 AgentBridge

| 扩展方式 | 如何实现 |
|---|---|
| 新 Schema 解析器 | 在 `discovery.py` 中实现发现器 |
| 新工具格式 | 在 `generator.py` 中添加构建函数 |
| 自定义 AI 提示词 | 在 `agent.py` 中覆盖默认提示词 |
| 自定义风险策略 | 修改 `policy.py` |
| 自定义 Agent 工具 | 扩展 `AgentRunner._build_kit_tools()` |

---

## 📦 发布与安装

<details>
<summary>🔧 `pip install "agbr"` 是如何工作的？</summary>

AgentBridge 使用 **`pyproject.toml`** + **setuptools** 打包为标准 Python 包。以下是机制说明：

### 1. 包结构

```text
AgentBridge/
├── pyproject.toml          # 包元数据、依赖、入口点
├── src/
│   └── agentbridge/        # 实际的 Python 包
│       ├── __init__.py
│       ├── cli.py          # CLI 入口点
│       ├── agent.py
│       ├── generator.py
│       └── ...
└── tests/
```

### 2. `pyproject.toml` 关键配置

```toml
[project]
name = "agbr"                    # pip install agbr
version = "0.2.0"

[project.optional-dependencies]         # pip install "agbr[agent]"
agent = ["claude-agent-sdk>=0.1.0"]
ai = ["anthropic>=0.30.0"]

[project.scripts]
agentbridge = "agentbridge.cli:main"    # CLI 入口点

[tool.setuptools.packages.find]
where = ["src"]                         # 代码位于 src/
```

### 3. `pip install` 的工作流程

1. **构建**：`pip` 读取 `pyproject.toml`，使用 `setuptools` 构建 wheel（`.whl`）
2. **安装**：wheel 被安装到 Python 环境的 `site-packages/` 目录
3. **CLI**：`[project.scripts]` 配置在 PATH 中创建 `agentbridge` 可执行文件，调用 `agentbridge.cli:main`

### 4. 发布到 PyPI（使任何人都可以 `pip install`）

```bash
# 构建包
pip install build
python -m build

# 上传到 TestPyPI（测试用）
pip install twine
twine upload --repository testpypi dist/*

# 上传到 PyPI（正式发布）
twine upload dist/*
```

发布后，任何人都可以运行 `pip install "agbr"`。

### 5. 未发布到 PyPI 时的安装方式

在包发布到 PyPI 之前，用户可以通过以下方式安装：

```bash
# 从本地源码安装（可编辑模式，开发用）
pip install -e .

# 从 GitHub 仓库安装
pip install git+ssh://git@github.com/jastfkjg/AgentBridge.git

# 从本地 wheel 安装
pip install dist/agentbridge-0.2.0-py3-none-any.whl
```

</details>

---

## 🤝 参与贡献

欢迎贡献！参与方式：

1. **Fork** 本仓库
2. **创建** 功能分支（`git checkout -b feature/amazing-feature`）
3. **提交** 更改（`git commit -m 'Add amazing feature'`）
4. **推送** 到分支（`git push origin feature/amazing-feature`）
5. **发起** Pull Request

请确保测试通过：

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

---

## 📄 许可证

本项目基于 MIT 许可证 — 详见 [LICENSE](LICENSE) 文件。
