# OpenAPI to Runnable MCP Server

AgentBridge's first MVP path turns an existing OpenAPI schema into an MCP environment that Claude, Codex, or any MCP client can operate.

## Quick Start

Generate a schema-only kit without configuring an LLM:

```bash
agentbridge generate openapi.json --output .agentbridge/openapi-kit --no-ai
```

Run it as a stdio MCP Server:

```bash
agentbridge serve .agentbridge/openapi-kit
```

This OpenAPI path defaults to dry-run mode and does not call the target system. For full project directory understanding, configure an AI backend so AgentBridge can reason over code semantics with scanner output as supporting evidence.

Connect it to a real HTTP system:

```bash
agentbridge serve .agentbridge/openapi-kit \
  --base-url http://localhost:8080 \
  --bearer-env API_TOKEN \
  --execute
```

Generate client configuration snippets:

```bash
agentbridge mcp-config .agentbridge/openapi-kit \
  --base-url http://localhost:8080 \
  --bearer-env API_TOKEN \
  --execute
```

You can also pass custom headers:

```bash
agentbridge serve .agentbridge/openapi-kit \
  --base-url http://localhost:8080 \
  --header "X-Tenant=demo" \
  --header "X-Request-Source=agentbridge" \
  --execute
```

## Modes

| Mode | Command | Behavior |
|---|---|---|
| Dry-run | `agentbridge serve <kit>` | MCP tool calls return planned calls only |
| Execute | `agentbridge serve <kit> --base-url <url> --execute` | HTTP transport tools call the target system |

## Safety

- `serve` defaults to dry-run.
- Real HTTP requests only happen with `--execute`.
- `destructive` and `external_side_effect` tools require `confirmed: true` in the MCP tool arguments.
- Bearer tokens and headers are runtime inputs. Prefer `--bearer-env API_TOKEN` so configs store only the environment variable name.
- `--read-only` blocks write/destructive/external-side-effect tools.
- `--deny-risk` disables one or more risk levels.
- `--allow-tool` restricts runtime calls to selected tools.
- `--audit-log` writes JSONL tool-call audit events.
- Dry-run responses include a request preview with method, URL, redacted headers, body, and risk reason when the tool uses HTTP transport.

Before connecting an agent, run:

```bash
agentbridge validate .agentbridge/openapi-kit
agentbridge doctor .agentbridge/openapi-kit --execute --base-url http://localhost:8080
```

## HTTP Mapping

OpenAPI HTTP transports are mapped into requests:

- Path params: `/projects/{project_id}/chapters` + `{"project_id":"p1"}` -> `/projects/p1/chapters`
- Remaining GET/HEAD/OPTIONS args become query parameters
- Remaining POST/PUT/PATCH/DELETE args become JSON body
- `--bearer-token` sets `Authorization: Bearer ...` directly.
- `--bearer-env API_TOKEN` reads the token from an environment variable at runtime and is preferred for client config snippets.
- `--header NAME=VALUE` may be repeated

## MCP Capabilities

`agentbridge serve` exposes stdio JSON-RPC MCP methods:

- `initialize`
- `tools/list`
- `tools/call`

`tools/list` converts `capabilities.json` into MCP tools. High-risk tools include an extra `confirmed` parameter so clients can express explicit human confirmation.

## MVP Boundary

Current focus:

- Implemented: OpenAPI discovery, kit generation, stdio MCP server, HTTP execution, dry-run, confirmation parameter.
- Planned: GraphQL adapter, database adapter, and richer agent planning.
