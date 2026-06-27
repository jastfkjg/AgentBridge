# Chat Entrypoints

AgentBridge chat entrypoints are Claude Agent control surfaces over a generated Agent Integration Kit. CLI chat and Web Chat use the same local runtime:

- parsed system capabilities from `capabilities.json`
- guardrails from `guardrails/permissions.json`
- tool calls through the AgentBridge runtime
- optional HTTP execution through `--execute`
- session memory and pending high-risk confirmations

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

High-risk operations pause before execution and show the planned call, risk reason, request URL, redacted headers, body, and arguments. Type `confirm` to continue or `cancel` to clear the pending operation.

## Web Chat

```bash
agentbridge web .agentbridge/openapi-kit --port 8765
```

Open the printed URL in a browser. The UI exposes the parsed system capabilities as a browser-based Claude Agent chat control surface and includes:

- user and session selectors
- active kit display
- Dry-run / Real system runtime selector
- Base URL validation and target-system connectivity test
- clickable tool list that inserts `/run` commands and required parameters
- chat transcript
- visible Authorize/Cancel controls for pending operations
- Claude Agent SDK token usage for the last response and current session

Run in execution mode:

```bash
agentbridge web .agentbridge/openapi-kit \
  --base-url http://localhost:8080 \
  --bearer-env API_TOKEN \
  --execute \
  --read-only
```

The mode can also be changed directly in the Web UI. Real system mode requires an `http://` or `https://` Base URL. The connectivity test sends `HEAD` to the Base URL and falls back to `GET` when `HEAD` is not supported; any HTTP response means the system is reachable. Switching modes clears any pending confirmation before rebuilding the runtime, while guardrails and human confirmation remain enforced.

Terminal chat exposes the same core workflows through `/use`, `/mode`, `/connect`, and `/usage`. `/use` lists numbered tools and prompts for required parameters. High-risk calls present an explicit Authorize/Cancel selection.

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

Memory stores the recent transcript and any pending high-risk operation for the user/session/kit tuple.

## Confirmation Flow

1. The user asks for a high-risk operation.
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
