# Architecture

AgentBridge uses an AI-agent-first generation pipeline while keeping deterministic scanners as a candidate evidence layer.

## Flow

1. Candidate discoverers scan OpenAPI, GraphQL, SQL, routes, and database definitions.
2. Without an LLM, the static generator emits a runnable kit; with an LLM, the AI analysis agent reads project code and candidate evidence.
3. The AI agent produces project analysis, risk reasoning, enhanced capabilities, skills, and prompts.
4. The generator writes the `agentbridge-kit/v1` protocol directory.
5. `agentbridge serve` exposes the kit as a stdio MCP Server for Claude, Codex, or other MCP clients.
6. `agentbridge chat` and `agentbridge web` provide user-facing chat entrypoints over the same kit runtime.
7. Runtime tools enforce guardrails and dry-run checks before calling host-system adapters.

## Phase 1 MVP

The current MVP focuses on the shortest useful loop:

```bash
agentbridge generate openapi.json --output .agentbridge/openapi-kit --no-ai
agentbridge serve .agentbridge/openapi-kit --base-url http://localhost:8080 --execute
```

This path does not require an LLM. OpenAPI operations are normalized into capabilities, and the kit contains MCP tool definitions, guardrails, dry-run plans, skills, and a system prompt. `serve` defaults to dry-run; only `--execute` enables the HTTP adapter to call the target system.

## Why Keep Rules

Rules are cheap, deterministic evidence collectors, and they also support the no-LLM OpenAPI-to-MCP path. They should not be treated as the final business model. Understanding controller/service behavior, workflow intent, side effects, and implied operations belongs to the AI analysis layer.

## Runtime Boundary

Execution has two layers:

- Default mode: MCP tool calls return planned calls only, with no target-system side effects.
- Execute mode: `--execute` enables real HTTP calls, but high-risk tools still require `confirmed: true`.

Chat entrypoints add session memory and human-in-the-loop confirmation. High-risk operations are stored as pending calls until the user confirms or cancels them.

## Safety Boundary

Generation may infer tools, but runtime execution must obey `guardrails/permissions.json`. Generated assistants must not execute destructive or external-side-effect operations without explicit human confirmation.
