# AgentBridge Kit Protocol

Current protocol: `agentbridge-kit/v1`

An AgentBridge kit is the versioned contract between parsed existing-system capabilities and Claude-facing tool surfaces. Agent runtimes, MCP servers, SDK adapters, CI checks, and dry-run tools should read `manifest.json` first, then resolve files through its `outputs` field.

```text
Existing system evidence
  -> capabilities.json
  -> Agent Integration Kit files
  -> Claude Agent SDK / MCP / Web Chat tools
  -> guarded target-system operations
```

## Required Structure

```text
agent-kit/
  manifest.json
  capabilities.json
  analysis/
    rule_signals.json
    agent_analysis.json
    report.md
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
  clients/
    mcp-client-configs.json
    README.md
  dry_run_plan.json
```

## Semantics

- `analysis/rule_signals.json` stores candidate evidence from schema and source-route scanners.
- `analysis/agent_analysis.json` stores project understanding, workflows, assumptions, side effects, and risk reasoning from the AI agent or static generator.
- `analysis/report.md` is a human-readable summary of detected capabilities, risk counts, evidence, low-confidence items, and clarifying questions.
- `capabilities.json` is the normalized capability list used for tool generation and runtime execution. Capability entries may include `evidence` and `confidence` fields so operators can trace whether a tool came from OpenAPI, GraphQL, AST/source scanning, database schema, AI inference, or custom plugins.
- `tools/mcp_tools.json` can be exposed as stdio MCP tools by `agentbridge serve`.
- `tools/claude_tools.json` and generated prompts describe the same capabilities for Claude-facing integrations.
- `guardrails/permissions.json` is the authority for runtime safety decisions. It contains per-tool rules and a `policy.risk_actions` map. The generated default is `read=allow`, `write=confirm`, `destructive=deny`, and `external_side_effect=confirm`.
- `clients/mcp-client-configs.json` contains Claude/Codex/generic MCP setup snippets.
- `dry_run_plan.json` describes planned calls without real side effects.

## Optional Analysis Checkpoints

Large-project generation may also write:

- `analysis/resume_state.json`: current batch plan, completed batches, fallback/local-basic batches, remaining batches, and partial/complete status.
- `analysis/batches/*.json`: completed AI-enhancement or local basic batch outputs used by `--resume`; fallback/local-basic checkpoints can be retried when a working AI backend is configured.
- `analysis/preserved_user_files.json`: prompts, skills, or guardrail files that were preserved during regeneration, plus generated alternate files when relevant.

These files are additive and are not required for `agentbridge-kit/v1` consumers, but tools may read them to show progress or continue incomplete AI enhancement.

## MCP Server Runtime

`agentbridge serve <kit>` reads `manifest.json`, `capabilities.json`, and `guardrails/permissions.json`, then exposes the generated tool layer as MCP `tools/list` and `tools/call` over stdio JSON-RPC.

- By default it does not execute real requests and returns dry-run plans only.
- With `--execute`, HTTP transport tools call the target system pointed to by `--base-url`.
- `read` tools may execute in execute mode, `write` and `external_side_effect` tools require callers to pass `confirmed: true`, and `destructive` tools are denied by default unless the kit policy is edited.
- Runtime errors are returned with structured codes such as `permission_denied`, `schema_mismatch`, `http_error`, `timeout`, and `adapter_error`.
- The current execution adapters cover HTTP/OpenAPI, GraphQL, SQLite read-only SQL, gRPC through `grpcurl`, and explicit Python plugins.

## Chat Runtime

`agentbridge chat <kit>` and `agentbridge web <kit>` consume the same kit files and runtime guardrails to provide Claude Agent chat control surfaces over parsed system capabilities. Chat memory stores recent transcript and pending confirmations outside the stable protocol files, by default at `<kit>/.agentbridge-chat-memory.json`.

## Target Project Boundary

The kit is the only generated artifact. AgentBridge consumers and generators must not write into the target project during discovery or generation. Inputs are read-only evidence; outputs live under the user-selected kit directory.

## Compatibility

Consumers must validate the `protocol` field in `manifest.json`. Minor versions may add optional files, but required paths should remain stable for `agentbridge-kit/v1`. `agentbridge validate --migrate` applies additive v1 migrations, such as backfilling policy defaults and `analysis/report.md`, without bumping the protocol version.

## CI Quality

`agentbridge diff <old-kit> <new-kit>` compares added, removed, changed, and risk-changed capabilities plus guardrail changes. `agentbridge generate --check` generates into a temporary directory and exits non-zero when the existing output kit is stale.
