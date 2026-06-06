# AgentBridge Kit Protocol

Current protocol: `agentbridge-kit/v1`

An AgentBridge kit is a stable directory generated for an existing system. Agent runtimes, MCP servers, SDK adapters, CI checks, and dry-run tools should read `manifest.json` first, then resolve files through its `outputs` field.

## Required Structure

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

- `analysis/rule_signals.json` stores candidate evidence from schema and source-route scanners.
- `analysis/agent_analysis.json` stores project understanding, workflows, assumptions, side effects, and risk reasoning from the AI agent or static generator.
- `capabilities.json` is the normalized capability list used for tool generation and runtime execution.
- `tools/mcp_tools.json` can be exposed as stdio MCP tools by `agentbridge serve`.
- `guardrails/permissions.json` is the authority for runtime safety decisions.
- `dry_run_plan.json` describes planned calls without real side effects.

## MCP Server Runtime

`agentbridge serve <kit>` reads `manifest.json`, `capabilities.json`, and `guardrails/permissions.json`, then exposes MCP `tools/list` and `tools/call` over stdio JSON-RPC.

- By default it does not execute real requests and returns dry-run plans only.
- With `--execute`, HTTP transport tools call the target system pointed to by `--base-url`.
- `destructive` and `external_side_effect` tools require callers to pass `confirmed: true`.
- The current execution adapter focuses on OpenAPI/HTTP transports; GraphQL, database, and additional SDK adapters are planned.

## Compatibility

Consumers must validate the `protocol` field in `manifest.json`. Minor versions may add optional files, but required paths should remain stable for `agentbridge-kit/v1`.

