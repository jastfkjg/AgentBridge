<div align="center">

# 🌉 AgentBridge

**Turn your existing system into an AI-agent-ready platform — in seconds.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![PRs Welcome](https://img.shields.io/badge/PRs-Welcome-brightgreen.svg)](CONTRIBUTING.md)

[English](README.md) · [中文](README.zh-CN.md)

</div>

---

AgentBridge uses an AI analysis agent to understand your project code, then generates a complete **Agent Integration Kit** that works with MCP, Claude, OpenAI, and Vercel AI SDK out of the box.

Deterministic scanners still collect candidate evidence from OpenAPI, GraphQL, SQL, and source routes, but they are not the source of truth. The AI agent reads the project context, reasons about business objects and side effects, then creates tools, skills, prompts, guardrails, tests, and protocol metadata.

## 📑 Table of Contents

- [✨ Features](#-features)
- [🚀 Quick Start](#-quick-start)
- [📖 CLI Reference](#-cli-reference)
- [🔍 How AgentBridge Analyzes Projects](#-how-agentbridge-analyzes-projects)
- [🔍 Candidate Evidence Sources](#-candidate-evidence-sources)
- [📁 Stable Kit Protocol](#-stable-kit-protocol)
- [🤖 AI Agent Generation](#-ai-agent-generation)
- [🛡️ Safety Model](#-safety-model)
- [🏗️ Architecture](#-architecture)
- [📚 Documentation](#-documentation)
- [🧩 Extending AgentBridge](#-extending-agentbridge)
- [📦 Publishing & Installation](#-publishing--installation)
- [🤝 Contributing](#-contributing)
- [📄 License](#-license)

---

## ✨ Features

<table>
<tr><td width="50%">

🔍 **AI-first code analysis**
Uses an AI agent to interpret business objects, workflows, permissions, and side effects from project code

</td><td width="50%">

🔧 **Multi-format Generation**
Outputs MCP, Claude, OpenAI, and Vercel AI SDK tool definitions simultaneously

</td></tr>
<tr><td>

🤖 **AI-Powered Generation**
Uses Claude Agent SDK (primary) or Anthropic API (fallback) to dynamically generate tools, skills, and prompts

</td><td>

🧠 **Agent as a Service**
Runs as an interactive agent for your existing project via Claude Agent SDK

</td></tr>
<tr><td>

🛡️ **Safety-first**
Classifies operations by risk level with human-in-the-loop confirmation for dangerous actions

</td><td>

🧪 **Dry-run Validation**
Test tool invocations against generated guardrails before executing

</td></tr>
<tr><td>

🌐 **Custom LLM Providers**
Supports DeepSeek, OpenRouter, and any Anthropic-compatible endpoint

</td><td>

🪶 **Rules as evidence**
OpenAPI, GraphQL, SQL, and route scanners provide candidate signals for the AI agent to verify or override

</td></tr>
</table>

---

## 🚀 Quick Start

### Installation

```bash
pip install agbr
```

<details>
<summary>📦 Install with optional features</summary>

```bash
# AI-powered generation + agent sessions (recommended)
pip install "agbr[agent]"

# Lightweight AI generation (no Claude Agent SDK)
pip install "agbr[ai]"

# Everything
pip install "agbr[all]"

# Install from source (for development)
git clone git@github.com:jastfkjg/AgentBridge.git
cd AgentBridge
pip install -e ".[all]"
```

</details>

### Configure LLM Provider

AgentBridge relies on an AI backend for existing-project understanding. Deterministic scanners and regex/rule signals are used as evidence for the AI agent, not as the final project model. The schema-only OpenAPI-to-MCP path can run with `--no-ai`; directory-level project analysis should use Claude Agent SDK or another Anthropic-compatible provider.

```bash
# Required for project directory analysis
export ANTHROPIC_API_KEY="sk-ant-..."

# Optional: Custom API endpoint
export ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"   # DeepSeek
# export ANTHROPIC_BASE_URL="https://openrouter.ai/api/v1"       # OpenRouter

# Optional: Custom model name
export ANTHROPIC_MODEL="deepseek-v4-flash"
```

<details>
<summary>🔑 Or pass via CLI flags</summary>

```bash
agentbridge generate examples/writing_system --output build/kit \
  --api-key "sk-..." \
  --base-url "https://api.deepseek.com/anthropic" \
  --model "deepseek-v4-flash"
```

> **Note:** When `ANTHROPIC_BASE_URL` is set, AgentBridge automatically uses the `anthropic` SDK backend (not `claude-agent-sdk`) for generation, since custom endpoints are not supported by the agent SDK.

</details>

### Generate an Agent Integration Kit

```bash
# Project directory analysis uses AI. Generated files are written only to --output.
agentbridge generate examples/writing_system --output .agentbridge/writing-kit
```

### Run an MCP Server from OpenAPI

```bash
agentbridge generate openapi.json --output .agentbridge/openapi-kit --no-ai

# Dry-run by default, with no target-system side effects
agentbridge serve .agentbridge/openapi-kit

# Execute real HTTP calls against the target system
agentbridge serve .agentbridge/openapi-kit \
  --base-url http://localhost:8080 \
  --bearer-token "$API_TOKEN" \
  --execute
```

### Run as an Agent

```bash
agentbridge chat .agentbridge/writing-kit

# Browser chat UI
agentbridge web .agentbridge/writing-kit --port 8765
```

### Run Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

---

## 📖 CLI Reference

| Command | Description |
|---|---|
| `agentbridge discover <paths>` | Discover and print capabilities as JSON |
| `agentbridge generate <paths> -o <dir>` | Generate an Agent Integration Kit; uses AI enhancement when configured |
| `agentbridge serve <kit>` | Run a generated kit as a stdio MCP Server |
| `agentbridge dry-run <kit> <tool>` | Dry-run a tool invocation |
| `agentbridge chat <kit>` | Start an interactive CLI chat over the kit runtime |
| `agentbridge web <kit>` | Start a browser chat UI over the kit runtime |

<details>
<summary>📝 Full command details</summary>

### `discover`

```bash
agentbridge discover examples/writing_system
```

### `generate`

```bash
agentbridge generate examples/writing_system --output build/agent-kit

# No LLM, useful for schema-only OpenAPI-to-MCP Server kits
agentbridge generate examples/writing_system/openapi.json --output build/openapi-kit --no-ai

# With custom name
agentbridge generate examples/writing_system --output build/agent-kit --name my-kit

# With custom LLM provider
agentbridge generate examples/writing_system --output build/agent-kit \
  --api-key "sk-..." --base-url "https://api.deepseek.com/anthropic" --model "deepseek-v4-flash"
```

### `dry-run`

```bash
# Normal invocation
agentbridge dry-run build/agent-kit create_chapter --args '{"project_id":"p1","title":"Opening"}'

# High-risk operation (requires confirmation)
agentbridge dry-run build/agent-kit delete_character \
  --args '{"project_id":"p1","character_id":"c1"}' --confirmed
```

### `serve`

```bash
# stdio MCP Server, dry-run by default
agentbridge serve build/openapi-kit

# Execute real HTTP calls against the target system
agentbridge serve build/openapi-kit \
  --base-url http://localhost:8080 \
  --header "X-Tenant=demo" \
  --bearer-token "$API_TOKEN" \
  --execute
```

### `chat`

```bash
agentbridge chat build/agent-kit

# Execute real HTTP calls, with session memory
agentbridge chat build/agent-kit \
  --base-url http://localhost:8080 \
  --bearer-token "$API_TOKEN" \
  --execute \
  --user alice \
  --session demo
```

Inside chat, use `/tools`, `/run <tool> key=value`, `confirm`, `cancel`, and `/history`.

### `web`

```bash
agentbridge web build/agent-kit --port 8765

# Execute real HTTP calls in the Web UI
agentbridge web build/agent-kit \
  --base-url http://localhost:8080 \
  --bearer-token "$API_TOKEN" \
  --execute
```

</details>

---

## 🔍 How AgentBridge Analyzes Projects

AgentBridge is designed so the AI agent performs the main project understanding step. Rule-based discovery is intentionally conservative and acts as evidence collection.

| Stage | Role |
|---|---|
| Candidate scanning | Extract OpenAPI operations, GraphQL fields, SQL tables, and route handlers |
| AI project analysis | Infer business objects, workflows, permission boundaries, side effects, missing operations, and assumptions |
| Capability normalization | Convert the AI-enhanced analysis into stable tool-ready capabilities |
| Kit generation | Emit tools, skills, prompts, resource schemas, guardrails, dry-run plans, and tests |

AgentBridge does not modify the target project during discovery or generation. All generated artifacts are written under the caller-provided output directory, preferably outside the project or under a dedicated ignored directory such as `.agentbridge/`.

The generated kit preserves both layers:

- `analysis/rule_signals.json`: deterministic candidate evidence
- `analysis/agent_analysis.json`: AI agent project analysis and reasoning

## 🔍 Candidate Evidence Sources

| Source Type | Formats |
|---|---|
| 🌐 API Schemas | OpenAPI JSON/YAML, GraphQL schemas |
| 🗄️ Database Schemas | SQL `CREATE TABLE` statements |
| 🐍 Python Routes | FastAPI `@router.get/post/...`, Flask `@app.route` |
| 📜 JavaScript Routes | Express `app.get/post/...` |
| ☕ Java Routes | Spring `@GetMapping/@PostMapping/...` |

All sources are normalized into a common capability model with:

| Field | Description |
|---|---|
| `domain` + `resource` | Logical grouping |
| `action` | What the capability does |
| `input_schema` | JSON Schema parameters |
| `risk` | `read` / `write` / `destructive` / `external_side_effect` |
| `confirm_required` | Whether human approval is needed |
| `source` | Full traceability back to the origin file |

---

## 📁 Stable Kit Protocol

Current protocol: `agentbridge-kit/v1`. See [docs/kit-protocol.md](docs/kit-protocol.md).

```text
agent-kit/
├── manifest.json                  # Kit metadata and summary
├── capabilities.json              # AI-enhanced normalized capabilities
├── analysis/
│   ├── rule_signals.json          # Scanner evidence used as AI context
│   └── agent_analysis.json        # AI project analysis and risk reasoning
├── spec/
│   └── kit-protocol.md            # Protocol contract copied into the kit
├── tools/
│   ├── mcp_tools.json             # MCP tool definitions
│   ├── openai_tools.json          # OpenAI function calling format
│   ├── claude_tools.json          # Claude tool use format
│   └── vercel_ai_tools.ts         # Vercel AI SDK TypeScript tools
├── skills/
│   └── writing.md                 # Domain-specific skill definitions
├── prompts/
│   └── system.md                  # Agent system prompt
├── resources/
│   └── schema.json                # Resource schema summary
├── guardrails/
│   └── permissions.json           # Risk policy and confirmation rules
├── tests/
│   ├── tool_invocation_tests.json # Auto-generated invocation tests
│   └── test_generated_tools.py    # Python unit tests for tool contracts
└── dry_run_plan.json              # Dry-run execution plan
```

---

## 🤖 AI Agent Generation

AgentBridge uses an AI analysis agent to generate the semantic parts of the kit: project analysis, tool descriptions, skills, system prompts, risk assessments, and inferred tools. Rule-based analysis is passed to the agent as candidate evidence and safety hints, not copied directly as final output.

| What | Description |
|---|---|
| 🧭 **Project analysis** | Business objects, workflows, permission boundaries, side effects, and assumptions |
| 📝 **Enhanced tool descriptions** | Context-aware descriptions that capture business semantics |
| 🎯 **Domain-specific skills** | Workflow prompts tailored to your domain with best practices |
| 🧠 **Intelligent system prompts** | Prompts that understand resource relationships and suggest safe sequences |
| 🔍 **Inferred additional tools** | Tools implied by your schema but not explicitly present |
| ⚠️ **Improved risk assessments** | LLM evaluates risk with rule-based hints as context |

### AI Backend

| Backend | Package | Use Case |
|---|---|---|
| **Claude Agent SDK** (primary) | `claude-agent-sdk` | Generation + interactive agent sessions |
| **Anthropic API** (fallback) | `anthropic` | Generation only, supports custom endpoints |

When `claude-agent-sdk` is installed and no custom `ANTHROPIC_BASE_URL` is set, it is used automatically. When a custom endpoint is configured or `claude-agent-sdk` is not installed, the `anthropic` SDK is used.

### Programmatic Usage

```python
from pathlib import Path
from agentbridge.generator import AgentKitGenerator
from agentbridge.agent import AIGenerator, AgentRunner

# Generate with default provider (Anthropic Claude)
ai = AIGenerator(api_key="sk-ant-...")
kit = AgentKitGenerator(ai_generator=ai).generate(
    [Path("examples/writing_system")],
    Path("build/agent-kit"),
)

# Custom LLM provider (e.g., DeepSeek)
ai = AIGenerator(
    api_key="sk-d831ecabc21842fdae6f30c24dd3b052",
    base_url="https://api.deepseek.com/anthropic",
    model="deepseek-v4-flash",
)

# Agent session
import asyncio
async def main():
    runner = AgentRunner(kit_dir="build/agent-kit", api_key="sk-ant-...")
    async for message in runner.query("List all chapters in project p1"):
        print(message)
asyncio.run(main())
```

---

## 🛡️ Safety Model

| Risk Level | Confirmation | Examples |
|---|---|---|
| 🟢 `read` | Not required | GET, list, search, find |
| 🟡 `write` | Optional by policy | POST, create, update, rewrite |
| 🔴 `destructive` | **Required** | DELETE, remove, destroy, drop, cancel |
| 🟠 `external_side_effect` | **Required** | publish, send, email, pay, deploy, export |

The safety model is applied consistently. Rule-based risk classification provides initial context, and the LLM may override when justified.

---

## 🏗️ Architecture

```text
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│ Rule Signals │────▶│ AI Analysis │────▶│ Kit Generator   │
│ (schemas,    │     │ Agent       │     │ (protocol v1)   │
│  routes, SQL)│     │             │     │                 │
└─────────────┘     └──────┬──────┘     └────────┬────────┘
                           │                     │
                    ┌──────▼──────┐       ┌──────▼────────┐
                    │ Capabilities│       │ Agent Runtime │
                    │ Skills      │       │ Dry-run +     │
                    │ Guardrails  │       │ Guardrails    │
                    └─────────────┘       └───────────────┘
```

---

## 📚 Documentation

- [Architecture](docs/architecture.md)
- [Kit protocol](docs/kit-protocol.md)
- [OpenAPI to MCP Server](docs/mcp-server.md)
- [Chat entrypoints](docs/chat.md)
- [TODO / Roadmap](TODO.md)
- [中文 README](README.zh-CN.md)
- [中文架构说明](docs/architecture.zh-CN.md)
- [中文套件协议](docs/kit-protocol.zh-CN.md)
- [中文 OpenAPI 到 MCP Server](docs/mcp-server.zh-CN.md)
- [中文聊天入口](docs/chat.zh-CN.md)

---

## 🧩 Extending AgentBridge

| Extension | How |
|---|---|
| New schema parser | Implement a discoverer in `discovery.py` |
| New tool format | Add a builder function in `generator.py` |
| Custom AI prompts | Override prompts in `agent.py` |
| Custom risk policy | Modify `policy.py` |
| Custom agent tools | Extend `AgentRunner._build_kit_tools()` |

---

## 📦 Publishing & Installation

<details>
<summary>🔧 How does `pip install "agbr"` work?</summary>

AgentBridge is packaged as a standard Python package using **`pyproject.toml`** + **setuptools**. Here's the mechanism:

### 1. Package Structure

```text
AgentBridge/
├── pyproject.toml          # Package metadata, dependencies, entry points
├── src/
│   └── agentbridge/        # Actual Python package
│       ├── __init__.py
│       ├── cli.py          # CLI entry point
│       ├── agent.py
│       ├── generator.py
│       └── ...
└── tests/
```

### 2. `pyproject.toml` Key Sections

```toml
[project]
name = "agbr"                    # pip install agbr
version = "0.2.0"

[project.optional-dependencies]         # pip install "agbr[agent]"
agent = ["claude-agent-sdk>=0.1.0"]
ai = ["anthropic>=0.30.0"]

[project.scripts]
agentbridge = "agentbridge.cli:main"    # CLI entry point

[tool.setuptools.packages.find]
where = ["src"]                         # Code lives in src/
```

### 3. How `pip install` Works

1. **Build**: `pip` reads `pyproject.toml`, uses `setuptools` to build a wheel (`.whl`)
2. **Install**: The wheel is installed into your Python environment's `site-packages/`
3. **CLI**: The `[project.scripts]` section creates a `agentbridge` executable in your PATH that calls `agentbridge.cli:main`

### 4. Publishing to PyPI (so anyone can `pip install`)

```bash
# Build the package
pip install build
python -m build

# Upload to TestPyPI (for testing)
pip install twine
twine upload --repository testpypi dist/*

# Upload to PyPI (for real)
twine upload dist/*
```

After publishing, anyone can run `pip install "agbr"`.

### 5. Installing Without PyPI

Until the package is published on PyPI, users can install it in these ways:

```bash
# Install from local source (editable mode, for development)
pip install -e .

# Install from GitHub repository
pip install git+ssh://git@github.com/jastfkjg/AgentBridge.git

# Install from a local wheel
pip install dist/agentbridge-0.2.0-py3-none-any.whl
```

</details>

---

## 🤝 Contributing

Contributions are welcome! Here's how you can help:

1. **Fork** the repository
2. **Create** a feature branch (`git checkout -b feature/amazing-feature`)
3. **Commit** your changes (`git commit -m 'Add amazing feature'`)
4. **Push** to the branch (`git push origin feature/amazing-feature`)
5. **Open** a Pull Request

Please make sure tests pass:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
