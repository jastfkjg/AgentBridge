# AgentBridge

AgentBridge helps existing products add an AI assistant quickly. It discovers system capabilities from API schemas, database schemas, and source routes, then generates an **Agent Integration Kit**:

- MCP, Claude, OpenAI, and Vercel AI SDK style tool definitions
- Domain skills and workflow prompts
- Resource schemas
- Permission guardrails with risk levels
- Dry-run plans and tool invocation tests
- Human-in-the-loop confirmation policy for risky actions

The project is intentionally dependency-light. The core runs on Python standard library so it can be dropped into early-stage systems and CI jobs without a complex setup.

## Quick Start

```bash
PYTHONPATH=src python -m agentbridge generate examples/writing_system --output .agentbridge/writing-kit
```

Inspect generated files:

```bash
find .agentbridge/writing-kit -maxdepth 3 -type f | sort
```

Run tests:

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

## CLI

Discover capabilities as JSON:

```bash
PYTHONPATH=src python -m agentbridge discover examples/writing_system
```

Generate an Agent Integration Kit:

```bash
PYTHONPATH=src python -m agentbridge generate examples/writing_system --output build/agent-kit
```

Dry-run an invocation against generated guardrails:

```bash
PYTHONPATH=src python -m agentbridge dry-run build/agent-kit create_chapter --args '{"project_id":"p1","title":"Opening"}'
```

Use `--confirmed` for high-risk operations that require human approval.

## What AgentBridge Discovers

AgentBridge currently extracts capabilities from:

- OpenAPI JSON or YAML-like files
- GraphQL schemas with `Query` and `Mutation` fields
- SQL `CREATE TABLE` schemas
- Python/FastAPI/Flask style route decorators
- Java/Spring style controller mappings
- JavaScript/TypeScript Express-style routes

All sources are normalized into a common capability model with:

- domain and resource object
- action verb
- input schema
- risk level: `read`, `write`, `destructive`, or `external_side_effect`
- confirmation requirement
- source trace for auditability

## Generated Kit Layout

```text
agent-kit/
  manifest.json
  capabilities.json
  tools/
    mcp_tools.json
    openai_tools.json
    claude_tools.json
    vercel_ai_tools.ts
  skills/
    writing.md
  prompts/
    system.md
  resources/
    schema.json
  guardrails/
    permissions.json
  tests/
    tool_invocation_tests.json
    test_generated_tools.py
  dry_run_plan.json
```

## Safety Model

AgentBridge classifies operations by method names, HTTP verbs, route names, and side-effect keywords.

- `read`: no confirmation
- `write`: confirmation optional by policy
- `destructive`: confirmation required
- `external_side_effect`: confirmation required

Examples of high-risk operations include delete, publish, pay, refund, email, SMS, webhook, deploy, and export.

## Status

This is an initial implementation focused on deterministic generation and testable safety defaults. It is ready to extend with language-specific AST parsers, live API probing, SDK-specific executors, and deeper domain workflow generation.
