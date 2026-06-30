# MCP Runtime

MCP is one exposure path for the AgentBridge tool layer. A generated Agent Integration Kit can be served as stdio MCP tools so Claude, Codex, or any MCP-compatible client can inspect, dry-run, and safely operate parsed system capabilities.

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

GraphQL, SQL, and gRPC tools use transport-specific runtime targets:

```bash
agentbridge serve .agentbridge/my-system-kit \
  --graphql-endpoint http://localhost:8080/graphql \
  --database-url sqlite:///tmp/app.db \
  --grpc-target 127.0.0.1:50051 \
  --execute
```

## Modes

| Mode | Command | Behavior |
|---|---|---|
| Dry-run | `agentbridge serve <kit>` | MCP tool calls return planned calls only |
| Execute | `agentbridge serve <kit> --base-url <url> --execute` | HTTP transport tools call the target system |
| Execute GraphQL | `agentbridge serve <kit> --graphql-endpoint <url> --execute` | GraphQL tools POST generated query/mutation documents and variables |
| Execute SQL | `agentbridge serve <kit> --database-url sqlite:///tmp/app.db --execute` | SQL tools run read-only `SELECT` with automatic `LIMIT` |
| Execute gRPC | `agentbridge serve <kit> --grpc-target host:port --execute` | gRPC tools invoke `grpcurl` with JSON messages |

## Safety

- `serve` defaults to dry-run.
- Real runtime calls only happen with `--execute`.
- Generated policy defaults to read auto-execute, write confirmation, destructive denial, and external-side-effect confirmation.
- `write` and `external_side_effect` tools require `confirmed: true` in the MCP tool arguments. `destructive` tools are denied unless an operator edits the kit policy.
- Bearer tokens and headers are runtime inputs. Prefer `--bearer-env API_TOKEN` so configs store only the environment variable name.
- `--read-only` blocks write/destructive/external-side-effect tools.
- `--deny-risk` disables one or more risk levels.
- `--allow-tool` restricts runtime calls to selected tools.
- `--audit-log` writes JSONL tool-call audit events with user, session, model, tool call id, confirmation source, outcome, risk, and redacted arguments.
- Dry-run responses include a transport-specific request preview with redacted secrets and risk reason.
- Runtime failures use structured error codes: `permission_denied`, `schema_mismatch`, `http_error`, `timeout`, and `adapter_error`.

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

## Additional Runtime Adapters

- GraphQL tools are discovered from schema `Query` and `Mutation` fields. Runtime calls build an operation document, map capability arguments into GraphQL variables, and POST to `--graphql-endpoint` or `--base-url`.
- SQL tools discovered from `CREATE TABLE` statements are read-only. They generate `list_*` capabilities only, execute `SELECT`, support optional `id` filtering, and cap `limit` at the generated maximum.
- gRPC tools are discovered from `.proto` service methods and message fields. Execution shells out to `grpcurl`; dry-run previews never contact the target.
- Python plugin tools require an explicit plugin marker such as `AGENTBRIDGE_PLUGIN = True` or an `agentbridge_discover()` function. Plugin modules may provide `dry_run(capability, args, config)` and `execute(capability, args, config)`.

## MCP Capabilities

`agentbridge serve` exposes stdio JSON-RPC MCP methods:

- `initialize`
- `tools/list`
- `tools/call`

`tools/list` converts `capabilities.json` into MCP tools. Tools requiring human confirmation include an extra `confirmed` parameter so clients can express explicit approval.

## Current Boundary

Current execution support:

- Implemented: OpenAPI/HTTP, GraphQL, SQLite read-only SQL, gRPC through `grpcurl`, Python plugin adapter, dry-run, structured errors, audit redaction, and confirmation parameters.
- Planned: broader database dialect support, background-job adapter, and stronger agent planning.
