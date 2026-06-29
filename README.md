# AgentBridge

AgentBridge automatically parses existing projects and systems into a versioned tool layer controllable by Claude Agent chat. It discovers system evidence, normalizes it into capabilities, packages those capabilities as an Agent Integration Kit, and exposes the kit through Claude Agent SDK, MCP, terminal chat, and Web Chat.

```text
Existing project/system
  -> parse project/API/DB/GraphQL/job evidence
  -> normalized capabilities
  -> Agent Integration Kit
  -> Claude Agent SDK / MCP / Web Chat
  -> controlled APIs / DB / GraphQL / background jobs
```

[中文](README.zh-CN.md)

## What It Provides

- AI-first project analysis with Claude Agent SDK.
- Candidate discovery from OpenAPI, GraphQL, SQL, gRPC proto files, explicit Python plugins, and source routes.
- A stable `agentbridge-kit/v1` contract containing capabilities, tools, prompts, skills, schemas, guardrails, dry-run plans, client configs, and tests.
- Claude Agent SDK, MCP, Claude, OpenAI, and Vercel AI tool definitions generated from the same capability model.
- Browser and terminal chat control surfaces over the generated tool layer.
- Dry-run by default, runtime policy controls, and explicit authorization for high-risk operations.
- Session history, streaming Web Chat responses, tool timelines, clickable tool invocation, required-parameter guidance, file attachments, and AI token usage/cost metadata.
- In-place re-analysis of an existing kit when the source system changes.

Runtime adapters now cover HTTP/OpenAPI, GraphQL POST operations, SQLite read-only SQL SELECT tools, gRPC via `grpcurl`, and explicit Python plugin dry-run/execute hooks. Dry-run remains the default for every transport.

## Install

```bash
pip install "agbr[agent]"
```

Project analysis and `agentbridge enhance` require:

```bash
export ANTHROPIC_API_KEY="..."
```

Optional Anthropic-compatible endpoint:

```bash
export ANTHROPIC_BASE_URL="https://api.example.com/anthropic"
export ANTHROPIC_MODEL="your-model"
```

## Generate a Kit

Analyze a project directory with Claude Agent SDK:

```bash
agentbridge generate ./my-system \
  --output .agentbridge/my-system-kit \
  --analysis-mode agentic
```

For schema-only deterministic generation:

```bash
agentbridge generate ./openapi.json \
  --output .agentbridge/openapi-kit \
  --no-ai
```

Validate the result:

```bash
agentbridge validate .agentbridge/my-system-kit
```

## Enhance an Existing Kit

Re-analyze the current project and update the existing kit in place:

```bash
agentbridge enhance .agentbridge/my-system-kit ./my-system
```

This command always uses Claude Agent SDK. Existing AI-inferred capabilities are retained as a baseline, current project evidence is rescanned, duplicate operations are consolidated, and changed or new capabilities are regenerated.

Use `--resume` to reuse valid batch checkpoints:

```bash
agentbridge enhance .agentbridge/my-system-kit ./my-system --resume
```

## Start the Web Chat

```bash
agentbridge web .agentbridge/my-system-kit --port 8765
```

Open the printed URL. The Web UI acts as a Claude Agent chat control surface over the parsed system capabilities and supports:

- Dry-run and real-system mode switching.
- Base URL validation and connectivity testing.
- Saved login-account selection per kit.
- Clickable tools that insert `/run` commands and required parameters.
- Visible authorization buttons for high-risk operations and Claude Agent SDK tool permission prompts.
- Real-time SSE streaming, tool call timeline, interrupt control, recent conversations, file attachments, Markdown responses, and Claude Agent SDK model/token/cost usage.

Real-system mode still enforces generated guardrails and confirmation requirements.

Runtime credentials can be supplied when starting the server:

```bash
agentbridge web .agentbridge/my-system-kit \
  --base-url http://localhost:8080 \
  --bearer-env API_TOKEN \
  --execute
```

Web Chat stores the selected Base URL in `<kit>/.agentbridge-runtime.json` so reopening the same kit restores the target. When a real-system login call uses username/password arguments or returns `access_token`, `token`, `jwt`, an `Authorization` header, or `Set-Cookie`, Web Chat stores the runtime credentials in the same local state file. Multiple login accounts can be saved per kit; the Web UI exposes a saved-account selector and only sends the selected account id back to the server. This file is ignored by git and is not part of the generated kit protocol.

## Start Terminal Chat

```bash
agentbridge chat .agentbridge/my-system-kit
```

Useful commands:

```text
/tools
/use
/run <tool> key=value
/mode dry-run
/mode execute http://localhost:8080
/connect http://localhost:8080
/usage
/history
```

`/use` provides numbered tool selection and prompts for required parameters. High-risk operations provide an explicit Authorize/Cancel choice.

## Run as an MCP Server

Expose the generated Agent Integration Kit as MCP tools.

Dry-run:

```bash
agentbridge serve .agentbridge/my-system-kit
```

Real HTTP execution:

```bash
agentbridge serve .agentbridge/my-system-kit \
  --base-url http://localhost:8080 \
  --bearer-env API_TOKEN \
  --execute
```

GraphQL, SQL, and gRPC tools use transport-specific runtime targets:

```bash
agentbridge serve .agentbridge/my-system-kit \
  --graphql-endpoint http://localhost:8080/graphql \
  --database-url sqlite:///tmp/app.db \
  --grpc-target 127.0.0.1:50051 \
  --execute
```

Generate client configuration:

```bash
agentbridge mcp-config .agentbridge/my-system-kit --write
```

## Safety Defaults

- Dry-run never executes the target operation.
- Destructive and external-side-effect tools require explicit confirmation.
- Switching runtime mode clears pending authorization.
- Project analysis is read-only; generated files are written only to the selected kit directory.
- Secrets are runtime inputs and must not be stored in generated kit protocol files. Web Chat credentials live in local runtime state (`.agentbridge-runtime.json`), which should stay out of version control.

## Main Commands

| Command | Purpose |
| --- | --- |
| `discover <paths>` | Print deterministic candidate capabilities |
| `generate <paths> -o <kit>` | Generate a Claude-controllable Agent Integration Kit |
| `enhance <kit> <paths>` | Re-analyze and update an existing kit with Claude Agent SDK |
| `validate <kit>` | Validate kit protocol and safety contracts |
| `doctor <kit>` | Check runtime readiness |
| `web <kit>` | Start browser chat over the tool layer |
| `chat <kit>` | Start terminal chat over the tool layer |
| `serve <kit>` | Expose the kit as a stdio MCP server |
| `dry-run <kit> <tool>` | Preview one tool invocation |
| `mcp-config <kit>` | Generate MCP client configuration |

## Documentation

- [Architecture](docs/architecture.md)
- [Chat and Web UI](docs/chat.md)
- [MCP runtime](docs/mcp-server.md)
- [Kit protocol](docs/kit-protocol.md)
- [Chinese documentation](docs/architecture.zh-CN.md)

## Development

```bash
PYTHONPATH=src python -m unittest discover -s tests
PYTHONPATH=src python -m compileall src tests
```

## License

MIT
