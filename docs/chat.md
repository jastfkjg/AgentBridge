# Chat Entrypoints

AgentBridge chat entrypoints are Claude Agent control surfaces over a generated Agent Integration Kit. CLI chat and Web Chat use the same local runtime:

- parsed system capabilities from `capabilities.json`
- guardrails from `guardrails/permissions.json`
- tool calls through the AgentBridge runtime
- optional runtime execution through `--execute`
- session memory and pending confirmations for operations that policy marks as confirm-required

The goal is to let users talk to a Claude-powered agent that can inspect, plan, dry-run, and safely operate the existing system through the generated tool layer.

## CLI Chat

```bash
agentbridge chat .agentbridge/openapi-kit
```

The default mode is dry-run. Tool calls return planned operations without target-system side effects.

```bash
agentbridge chat .agentbridge/openapi-kit \
  --base-url http://localhost:8080 \
  --bearer-env API_TOKEN \
  --execute \
  --audit-log .agentbridge/audit.jsonl \
  --user alice \
  --session demo
```

Inside the chat:

```text
/tools
/run list_chapter project_id=p1
create_chapter project_id=p1 title="Opening"
delete_character project_id=p1 character_id=c1
confirm
cancel
/history
```

Operations marked `confirm` by policy pause before execution and show the planned call, risk reason, request URL, redacted headers, body, and arguments. Type `confirm` to continue or `cancel` to clear the pending operation. The generated default policy allows reads, requires confirmation for writes and external side effects, and denies destructive tools.

## System Control Console

```bash
agentbridge web .agentbridge/openapi-kit --port 8765
```

Open the printed URL in a browser. The Web home page is a responsive System Control Console with seven first-class, hash-addressable workspaces:

- **Chat** (`#chat`) keeps the Claude Agent transcript, SSE streaming, file attachments, interrupt control, recent conversations, and visible Authorize/Cancel controls.
- **Tools** (`#tools`) searches and filters generated tools by risk. **Prepare** inserts a typed `/run` command into Chat without executing it.
- **Capabilities** (`#capabilities`) exposes the normalized business contract with domain, source, confidence, transport context, and risk.
- **Policy** (`#policy`) summarizes the effective action for every risk class and edits `guardrails/permissions.json`.
- **Audit** (`#audit`) filters the most recent 200 redacted JSONL runtime events. Start with `--audit-log PATH` to enable capture.
- **Workflows** (`#workflows`) shows multi-step operating patterns from `analysis/agent_analysis.json`; these remain guidance and are never executed automatically.
- **Settings** (`#settings`) summarizes kit identity, protocol, runtime overrides, memory, configured adapters, saved-account management, and current-session token usage.

The persistent desktop navigation collapses to a full mobile navigation drawer below 760px. The Dry-run / Real system selector remains visible across all workspaces. Base URL validation, target connectivity testing, login-account selection, and account add/edit/delete controls continue to use the same guarded runtime.

Run in execution mode:

```bash
agentbridge web .agentbridge/openapi-kit \
  --base-url http://localhost:8080 \
  --bearer-env API_TOKEN \
  --execute \
  --read-only
```

The mode can also be changed directly in the Console. Real system mode requires an `http://` or `https://` Base URL. The connectivity test sends `HEAD` to the Base URL and falls back to `GET` when `HEAD` is not supported; any HTTP response means the system is reachable. Switching modes clears any pending confirmation before rebuilding the runtime, while guardrails and human confirmation remain enforced. Policy edits are written back to `guardrails/permissions.json`. The server prints concise request, stream, permission, and error logs to the terminal with secrets redacted.

`GET /api/console` supplies the read-only manifest, capability, workflow, audit, summary, and non-secret settings data used by the Console. Policy changes continue to use the dedicated `POST /api/policy` endpoint.

Terminal chat exposes the same core workflows through `/use`, `/mode`, `/connect`, and `/usage`. `/use` lists numbered tools and prompts for required parameters. Confirm-required calls present an explicit Authorize/Cancel selection.

GraphQL, SQL, and gRPC runtime targets can be supplied when starting CLI or Web Chat:

```bash
agentbridge chat .agentbridge/my-system-kit \
  --graphql-endpoint http://localhost:8080/graphql \
  --database-url sqlite:///tmp/app.db \
  --grpc-target 127.0.0.1:50051
```

Allow the browser UI to switch kit directories:

```bash
agentbridge web .agentbridge/openapi-kit --allow-kit-switch
```

## Memory

Chat memory is enabled by default and stored at:

```text
<kit>/.agentbridge-chat-memory.json
```

Options:

```bash
agentbridge chat .agentbridge/openapi-kit --session demo --user alice
agentbridge chat .agentbridge/openapi-kit --memory-file /tmp/agentbridge-memory.json
agentbridge chat .agentbridge/openapi-kit --no-memory
```

Memory stores the recent transcript and any pending confirm-required operation for the user/session/kit tuple.

## Confirmation Flow

1. The user asks for an operation that policy marks as `confirm`.
2. AgentBridge validates arguments and builds a dry-run plan.
3. The pending operation is stored in session memory.
4. CLI or Web UI shows risk, method/path, and arguments.
5. `confirm` repeats the call with `confirmed: true`; `cancel` clears it.

## Runtime Policy

The chat entrypoints accept the same runtime safety options as `serve`:

```bash
agentbridge chat .agentbridge/openapi-kit --read-only
agentbridge chat .agentbridge/openapi-kit --deny-risk destructive --deny-risk external_side_effect
agentbridge chat .agentbridge/openapi-kit --allow-tool list_chapter
agentbridge chat .agentbridge/openapi-kit --audit-log .agentbridge/audit.jsonl
```

Generated kit policy lives in `guardrails/permissions.json`. Its default `risk_actions` are:

```json
{
  "read": "allow",
  "write": "confirm",
  "destructive": "deny",
  "external_side_effect": "confirm"
}
```

Runtime errors use structured codes such as `permission_denied`, `schema_mismatch`, `http_error`, `timeout`, and `adapter_error`. Audit JSONL events include user/session/model/tool-call metadata and redact secrets before writing.
