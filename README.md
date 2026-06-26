# AgentBridge

AgentBridge analyzes an existing project and generates a versioned Agent Integration Kit containing tools, prompts, skills, schemas, guardrails, dry-run plans, and tests.

[中文](README.zh-CN.md)

## What It Provides

- AI-first project analysis with Claude Agent SDK.
- Candidate discovery from OpenAPI, GraphQL, SQL, and source routes.
- MCP, Claude, OpenAI, and Vercel AI tool definitions.
- Browser and terminal chat over the generated kit.
- Dry-run by default, runtime policy controls, and explicit authorization for high-risk operations.
- Session history, clickable tool invocation, required-parameter guidance, file attachments, and AI token usage.
- In-place re-analysis of an existing kit when the project changes.

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

Open the printed URL. The Web UI supports:

- Dry-run and real-system mode switching.
- Base URL validation and connectivity testing.
- Clickable tools that insert `/run` commands and required parameters.
- Visible authorization buttons for high-risk operations.
- Recent conversations, file attachments, Markdown responses, and Claude Agent SDK token usage.

Real-system mode still enforces generated guardrails and confirmation requirements.

Runtime credentials can be supplied when starting the server:

```bash
agentbridge web .agentbridge/my-system-kit \
  --base-url http://localhost:8080 \
  --bearer-env API_TOKEN \
  --execute
```

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

Generate client configuration:

```bash
agentbridge mcp-config .agentbridge/my-system-kit --write
```

## Safety Defaults

- Dry-run never executes the target operation.
- Destructive and external-side-effect tools require explicit confirmation.
- Switching runtime mode clears pending authorization.
- Project analysis is read-only; generated files are written only to the selected kit directory.
- Secrets are runtime inputs and must not be stored in generated kits.

## Main Commands

| Command | Purpose |
| --- | --- |
| `discover <paths>` | Print deterministic candidate capabilities |
| `generate <paths> -o <kit>` | Generate a new kit |
| `enhance <kit> <paths>` | Re-analyze and update an existing kit with Claude Agent SDK |
| `validate <kit>` | Validate kit protocol and safety contracts |
| `doctor <kit>` | Check runtime readiness |
| `web <kit>` | Start browser chat |
| `chat <kit>` | Start terminal chat |
| `serve <kit>` | Start stdio MCP server |
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
