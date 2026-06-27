# Architecture

AgentBridge turns an existing project or system into a Claude-controllable tool layer. The generation pipeline is AI-agent-first, while deterministic scanners remain the cheap evidence layer for APIs, schemas, routes, database definitions, and other system signals.

## Canonical Flow

```text
Existing project/system
  -> parse project/API/DB/GraphQL/job evidence
  -> normalized capabilities
  -> Agent Integration Kit
  -> Claude Agent SDK / MCP / Web Chat
  -> controlled APIs / DB / GraphQL / background jobs
```

1. Candidate discoverers scan OpenAPI, GraphQL, SQL, source routes, database definitions, and other system evidence.
2. For project directories, the AI analysis agent prefers Claude Agent SDK agentic exploration, reads project code and candidate evidence, and can work in batches with resume checkpoints. For schema-only inputs, `--no-ai` can emit a runnable deterministic kit.
3. The AI agent produces project analysis, risk reasoning, enhanced capabilities, skills, prompts, and optional batch checkpoints for large projects.
4. The generator writes the `agentbridge-kit/v1` protocol directory. This kit is the versioned contract between parsed system capabilities and agent-facing tool surfaces.
5. `agentbridge serve` exposes the kit as a stdio MCP Server for Claude, Codex, or other MCP clients.
6. `agentbridge chat` and `agentbridge web` provide Claude Agent chat control surfaces over the same kit runtime.
7. Runtime tools enforce guardrails and dry-run checks before calling host-system adapters.

## Current MVP

The current shortest useful loop is OpenAPI/HTTP to a runnable MCP or chat control surface:

```bash
agentbridge generate openapi.json --output .agentbridge/openapi-kit --no-ai
agentbridge serve .agentbridge/openapi-kit --base-url http://localhost:8080 --execute
```

This schema-only path does not require an LLM. OpenAPI operations are normalized into capabilities, and the kit contains MCP tool definitions, guardrails, dry-run plans, skills, and a system prompt. `serve` defaults to dry-run; only `--execute` enables the HTTP adapter to call the target system.

GraphQL, database, and background-job evidence can be discovered and represented as capabilities today. Real execution currently focuses on HTTP/OpenAPI transports; additional execution adapters are planned.

## Why Keep Rules

Rules are cheap, deterministic evidence collectors, and they also support the no-LLM OpenAPI-to-MCP path. They should not be treated as the final business model. Understanding controller/service behavior, workflow intent, side effects, and implied operations belongs to the AI analysis layer.

## Large-Project Analysis

AgentBridge splits large project analysis into ranked batches. The first batch targets the main capabilities, then the CLI can ask whether to continue enhancing the remaining batches. Claude Agent SDK batches stream read-only tool calls, file reads, code searches, and tool results into CLI progress output and `generation_status.json`. Batch progress is recorded under `analysis/resume_state.json` and `analysis/batches/*.json`, and `--resume` skips batches that already completed. If a Claude Agent SDK plan or batch hangs, AgentBridge times it out, switches to local basic project analysis, generates a usable kit, and can retry fallback or local-basic checkpoints later when a working AI backend is available.

`agentbridge enhance <kit> <paths>` updates an existing kit in place. It requires Claude Agent SDK, merges current scanner evidence with existing AI-inferred capabilities, consolidates duplicate transport operations, and regenerates the protocol files.

Runtime loading also normalizes legacy numeric suffixes such as `_2` and `_3`: identical transport operations are consolidated, distinct operations receive semantic names, and guardrail rules are remapped to the normalized tool names.

`--analysis-mode auto` prefers Claude Agent SDK when `claude-agent-sdk` is installed, including when `ANTHROPIC_BASE_URL` points to an Anthropic-compatible endpoint such as DeepSeek. `--analysis-mode agentic` requires the SDK route and also passes the compatible endpoint through to the SDK; `--analysis-mode prompt` forces direct prompt-based generation.

## Project Write Boundary

AgentBridge must not modify the target project during discovery or generation. All generated artifacts are written only under the caller-provided output directory. If the output directory is inside the scanned project, it must be a dedicated integration directory such as `.agentbridge/` or `agentbridge-kit/`.

## Runtime Boundary

Execution has two layers:

- Default mode: MCP and chat tool calls return planned calls only, with no target-system side effects.
- Execute mode: `--execute` enables real HTTP calls, but high-risk tools still require `confirmed: true`.

Chat entrypoints add session memory and human-in-the-loop confirmation. High-risk operations are stored as pending calls until the user confirms or cancels them.

## Safety Boundary

Generation may infer tools, but runtime execution must obey `guardrails/permissions.json`. Generated assistants must not execute destructive or external-side-effect operations without explicit human confirmation.
