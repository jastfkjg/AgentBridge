# Chat Entrypoints

AgentBridge can expose a generated kit through interactive chat. Both CLI and Web chat use the same local runtime:

- kit capabilities from `capabilities.json`
- guardrails from `guardrails/permissions.json`
- MCP tool calls through the AgentBridge runtime
- optional HTTP execution through `--execute`
- session memory and pending high-risk confirmations

## CLI Chat

```bash
agentbridge chat .agentbridge/openapi-kit
```

The default mode is dry-run. Tool calls return planned operations without target-system side effects.

```bash
agentbridge chat .agentbridge/openapi-kit \
  --base-url http://localhost:8080 \
  --bearer-token "$API_TOKEN" \
  --execute \
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

High-risk operations pause before execution and show the planned call. Type `confirm` to continue or `cancel` to clear the pending operation.

## Web Chat

```bash
agentbridge web .agentbridge/openapi-kit --port 8765
```

Open the printed URL in a browser. The UI includes:

- user and session selectors
- active kit display
- tool list
- chat transcript
- pending-operation confirmation panel

Run in execution mode:

```bash
agentbridge web .agentbridge/openapi-kit \
  --base-url http://localhost:8080 \
  --bearer-token "$API_TOKEN" \
  --execute
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

Memory stores the recent transcript and any pending high-risk operation for the user/session/kit tuple.

## Confirmation Flow

1. The user asks for a high-risk operation.
2. AgentBridge validates arguments and builds a dry-run plan.
3. The pending operation is stored in session memory.
4. CLI or Web UI shows risk, method/path, and arguments.
5. `confirm` repeats the call with `confirmed: true`; `cancel` clears it.

