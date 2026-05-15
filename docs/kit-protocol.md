# AgentBridge Kit Protocol

Current protocol: `agentbridge-kit/v1`

An AgentBridge kit is a stable directory generated for an existing system. Agent runtimes, MCP servers, SDK adapters, CI checks, and dry-run tools should consume the kit through `manifest.json` and the files referenced by `outputs`.

## Required Layout

```text
agent-kit/
  manifest.json
  capabilities.json
  analysis/
    rule_signals.json
    agent_analysis.json
  spec/
    kit-protocol.md
  tools/
    mcp_tools.json
    openai_tools.json
    claude_tools.json
    vercel_ai_tools.ts
  skills/
    *.md
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

## Semantics

- `analysis/rule_signals.json` stores scanner evidence from schemas and source routes.
- `analysis/agent_analysis.json` stores the AI agent's project understanding, workflows, assumptions, side effects, and risk reasoning.
- `capabilities.json` is the normalized post-analysis capability list used to generate tools.
- `guardrails/permissions.json` is authoritative for runtime safety.
- `dry_run_plan.json` describes planned calls without executing side effects.

## Compatibility

Consumers must verify the `protocol` field in `manifest.json`. Minor additions may introduce new optional files, but existing required paths should stay stable within `agentbridge-kit/v1`.

