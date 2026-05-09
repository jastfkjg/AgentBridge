# AgentBridge

**[中文文档](README.zh-CN.md)**

**Turn your existing system into an AI-agent-ready platform — in seconds.**

AgentBridge discovers capabilities from your API schemas, database schemas, and source routes, then generates a complete **Agent Integration Kit** that works with MCP, Claude, OpenAI, and Vercel AI SDK out of the box.

With built-in **Claude Agent SDK** support and **AI-powered dynamic generation**, AgentBridge goes beyond static templates — it uses AI to craft richer tool definitions, domain-specific skills, and intelligent prompts tailored to your system.

---

## ✨ Features

- **Auto-discovery** — Extracts capabilities from OpenAPI, GraphQL, SQL, and source code routes (Python/Flask/FastAPI, JS/Express, Java/Spring)
- **Multi-format tool generation** — Outputs MCP, Claude, OpenAI, and Vercel AI SDK tool definitions simultaneously
- **AI-powered generation** — Uses Claude Agent SDK (primary) or Anthropic API (fallback) to dynamically generate richer tools, skills, and prompts instead of relying on hardcoded templates
- **Agent as a Service** — Uses Claude Agent SDK to run as an interactive agent for your existing project, combining generated tools and skills
- **Safety-first** — Classifies operations by risk level (`read` / `write` / `destructive` / `external_side_effect`) with human-in-the-loop confirmation for dangerous actions
- **Dry-run validation** — Test tool invocations against generated guardrails before executing
- **Dependency-light core** — The deterministic generator runs on Python standard library only; AI features require an API key

---

## 🚀 Quick Start

### Installation

```bash
pip install agentbridge
```

For AI-powered generation and agent sessions (recommended):

```bash
pip install agentbridge[agent]
```

Lightweight AI generation without Claude Agent SDK:

```bash
pip install agentbridge[ai]
```

Install everything:

```bash
pip install agentbridge[all]
```

### Configure LLM Provider

AgentBridge requires an LLM API key for AI features. By default it uses Anthropic's Claude, but you can configure any Anthropic-compatible provider (DeepSeek, OpenRouter, etc.):

```bash
# Required: API key
export ANTHROPIC_API_KEY="sk-ant-..."

# Optional: Custom API endpoint (for DeepSeek, OpenRouter, etc.)
# DeepSeek Anthropic endpoint:
export ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"
# OpenRouter:
export ANTHROPIC_BASE_URL="https://openrouter.ai/api/v1"

# Optional: Custom model name
export ANTHROPIC_MODEL="deepseek-v4-flash"
```

Or pass them via CLI flags:

```bash
agentbridge generate examples/writing_system --output build/kit --ai \
  --api-key "sk-..." \
  --base-url "https://api.deepseek.com/anthropic" \
  --model "deepseek-v4-flash"
```

> **Note:** When `ANTHROPIC_BASE_URL` is set, AgentBridge automatically uses the `anthropic` SDK backend (not `claude-agent-sdk`) for generation, since custom endpoints are not supported by the agent SDK.

### Generate an Agent Integration Kit

```bash
# Deterministic generation (no API key needed)
agentbridge generate examples/writing_system --output .agentbridge/writing-kit

# AI-powered generation (requires ANTHROPIC_API_KEY)
agentbridge generate examples/writing_system --output .agentbridge/writing-kit --ai
```

### Run as an Agent

```bash
# Start an interactive AI agent session for your project
agentbridge chat .agentbridge/writing-kit
```

### Explore Generated Files

```bash
find .agentbridge/writing-kit -maxdepth 3 -type f | sort
```

### Run Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

---

## 📖 CLI Reference

### Discover capabilities

Print all discovered capabilities as JSON:

```bash
agentbridge discover examples/writing_system
```

### Generate an Agent Integration Kit

```bash
# Deterministic
agentbridge generate examples/writing_system --output build/agent-kit

# AI-powered (requires ANTHROPIC_API_KEY)
agentbridge generate examples/writing_system --output build/agent-kit --ai

# With custom name
agentbridge generate examples/writing_system --output build/agent-kit --name my-kit
```

### Dry-run a tool invocation

```bash
agentbridge dry-run build/agent-kit create_chapter --args '{"project_id":"p1","title":"Opening"}'
```

Use `--confirmed` for high-risk operations that require human approval:

```bash
agentbridge dry-run build/agent-kit delete_character --args '{"project_id":"p1","character_id":"c1"}' --confirmed
```

### AI agent chat

Start an interactive AI agent session powered by Claude Agent SDK:

```bash
agentbridge chat build/agent-kit
```

---

## 🔍 What AgentBridge Discovers

| Source Type | Formats |
|---|---|
| API Schemas | OpenAPI JSON/YAML, GraphQL schemas |
| Database Schemas | SQL `CREATE TABLE` statements |
| Python Routes | FastAPI `@router.get/post/...`, Flask `@app.route` |
| JavaScript Routes | Express `app.get/post/...` |
| Java Routes | Spring `@GetMapping/@PostMapping/...` |

All sources are normalized into a common capability model with:

- **domain** and **resource** — logical grouping
- **action** verb — what the capability does
- **input schema** — JSON Schema parameters
- **risk level** — `read`, `write`, `destructive`, or `external_side_effect`
- **confirmation requirement** — whether human approval is needed
- **source trace** — full auditability back to the origin file

---

## 📁 Generated Kit Layout

```text
agent-kit/
  manifest.json              # Kit metadata and summary
  capabilities.json          # All discovered capabilities
  tools/
    mcp_tools.json           # MCP tool definitions
    openai_tools.json        # OpenAI function calling format
    claude_tools.json        # Claude tool use format
    vercel_ai_tools.ts       # Vercel AI SDK TypeScript tools
  skills/
    writing.md               # Domain-specific skill definitions
  prompts/
    system.md                # Agent system prompt
  resources/
    schema.json              # Resource schema summary
  guardrails/
    permissions.json         # Risk policy and confirmation rules
  tests/
    tool_invocation_tests.json   # Auto-generated invocation tests
    test_generated_tools.py      # Python unit tests for tool contracts
  dry_run_plan.json          # Dry-run execution plan
```

---

## 🤖 AI-Powered Generation

When you use `--ai`, AgentBridge uses Claude to dynamically generate:

- **Enhanced tool descriptions** — Context-aware descriptions that capture business semantics, not just HTTP method + path
- **Domain-specific skills** — Workflow prompts tailored to your domain with best practices and edge-case handling
- **Intelligent system prompts** — Prompts that understand the relationships between your resources and suggest safe operation sequences
- **Additional inferred tools** — Tools that are implied by your schema but not explicitly present (e.g., search, batch operations)
- **Improved risk assessments** — Context-aware risk classification that goes beyond keyword matching

### AI Backend

AgentBridge supports two AI backends, auto-detected at runtime:

| Backend | Package | Use Case |
|---|---|---|
| **Claude Agent SDK** (primary) | `claude-agent-sdk` | Generation + interactive agent sessions |
| **Anthropic API** (fallback) | `anthropic` | Generation only, supports custom endpoints |

When `claude-agent-sdk` is installed and no custom `ANTHROPIC_BASE_URL` is set, it is used automatically for both generation and agent sessions. When a custom endpoint is configured or `claude-agent-sdk` is not installed, the `anthropic` SDK is used for generation.

### Configuration

LLM API key is **required** for AI features. Configure via environment variables:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."          # Required
export ANTHROPIC_BASE_URL=""                    # Optional: custom endpoint
export ANTHROPIC_MODEL="claude-sonnet-4-20250514"  # Optional: model name
```

Or pass programmatically:

```python
from agentbridge.agent import AIGenerator

# Default: Anthropic Claude
ai = AIGenerator(api_key="sk-ant-...")

# Custom provider (e.g., DeepSeek)
ai = AIGenerator(
    api_key="sk-d831ecabc21842fdae6f30c24dd3b052",
    base_url="https://api.deepseek.com/anthropic",
    model="deepseek-v4-flash",
)

# Custom provider (e.g., OpenRouter)
ai = AIGenerator(
    api_key="sk-or-...",
    base_url="https://openrouter.ai/api/v1",
    model="anthropic/claude-sonnet-4-20250514",
)
```

### Programmatic Usage

```python
from pathlib import Path
from agentbridge.generator import AgentKitGenerator
from agentbridge.agent import AIGenerator

# Deterministic generation (no API key needed)
kit = AgentKitGenerator().generate(
    [Path("examples/writing_system")],
    Path("build/agent-kit"),
)

# AI-powered generation (API key required)
ai = AIGenerator(api_key="sk-ant-...")
kit = AgentKitGenerator(ai_generator=ai).generate(
    [Path("examples/writing_system")],
    Path("build/agent-kit"),
)
```

### Claude Agent SDK Integration

Use AgentBridge as an interactive agent for your existing project:

```python
import asyncio
from agentbridge.agent import AgentRunner

async def main():
    runner = AgentRunner(kit_dir="build/agent-kit", api_key="sk-ant-...")
    async for message in runner.query("List all chapters in project p1"):
        print(message)

asyncio.run(main())
```

The `AgentRunner` uses `ClaudeSDKClient` under the hood, registering your generated tools as in-process MCP servers. This means Claude can directly invoke your project's capabilities as tools during the conversation.

---

## 🛡️ Safety Model

AgentBridge classifies operations by method names, HTTP verbs, route names, and side-effect keywords:

| Risk Level | Confirmation | Examples |
|---|---|---|
| `read` | Not required | GET, list, search, find |
| `write` | Optional by policy | POST, create, update, rewrite |
| `destructive` | **Required** | DELETE, remove, destroy, drop, cancel |
| `external_side_effect` | **Required** | publish, send, email, pay, deploy, export |

The safety model is applied consistently across both deterministic and AI-generated outputs. AI-generated tools are always validated against the same guardrails.

---

## 🏗️ Architecture

```text
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│  Discovery   │────▶│  Generator   │────▶│  Agent Kit      │
│  (schemas,   │     │  (determin-  │     │  (tools, skills,│
│   routes,    │     │   istic + AI)│     │   prompts,      │
│   SQL)       │     │              │     │   guardrails)   │
└─────────────┘     └──────┬───────┘     └────────┬────────┘
                           │                      │
                    ┌──────▼───────┐       ┌──────▼────────┐
                    │  AI Generator│       │  Agent Runner │
                    │  (Claude SDK │       │  (Claude SDK  │
                    │   / Anthropic│       │   Client +    │
                    │   API)       │       │   MCP Tools)  │
                    └──────────────┘       └───────────────┘
```

---

## 🧩 Extending AgentBridge

- **Add a new schema parser** — Implement a discoverer in `discovery.py` following the existing pattern
- **Add a new tool format** — Add a builder function in `generator.py`
- **Custom AI prompts** — Override the default AI generation prompts in `agent.py`
- **Custom risk policy** — Modify `policy.py` to change how risk levels are classified
- **Custom agent tools** — Extend `AgentRunner._build_kit_tools()` to add custom tool handlers

---

## 📋 Requirements

- Python 3.10+
- **AI generation (required for `--ai`):** `claude-agent-sdk` (recommended) or `anthropic` + `ANTHROPIC_API_KEY`
- **Agent sessions (required for `chat`):** `claude-agent-sdk` + `ANTHROPIC_API_KEY`

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.
