# P2-P4 Governance Analysis CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Implement all current `TODO.md` P2, P3, and P4 requirements for runtime governance, project understanding, and kit CI quality.

**Architecture:** Extend the existing kit protocol additively: keep `agentbridge-kit/v1`, add richer policy, evidence, analysis report, diff, and migration files without removing required paths. Runtime enforcement remains centralized in `runtime.py` and `mcp_server.py`; discovery/schema intelligence remains in `discovery.py`; kit quality commands live in `kit.py` and `cli.py`.

**Tech Stack:** Python standard library, `unittest`, existing AgentBridge dataclasses, JSON kit files, current no-network unit test style.

---

### Task 1: Runtime Policy, Errors, Audit, and Web Editing

**Files:**
- Create: `src/agentbridge/audit.py`
- Create: `src/agentbridge/errors.py`
- Modify: `src/agentbridge/policy.py`
- Modify: `src/agentbridge/runtime.py`
- Modify: `src/agentbridge/mcp_server.py`
- Modify: `src/agentbridge/generator.py`
- Modify: `src/agentbridge/web.py`
- Test: `tests/test_mcp_server.py`
- Test: `tests/test_chat_web.py`

- [x] **Step 1: Write failing tests**

```python
def test_policy_requires_write_confirmation_and_denies_destructive_by_default():
    server = AgentBridgeMCPServer(MCPServerConfig(kit_dir=kit, execute=True, base_url="http://example.test"))
    write_payload = call_tool(server, "create_chapter", {"project_id": "p1", "title": "Opening"})
    assert write_payload["error"]["code"] == "permission_denied"
    assert write_payload["requires_confirmation"] is True
    destructive_payload = call_tool(server, "delete_character", {"project_id": "p1", "character_id": "c1", "confirmed": True})
    assert destructive_payload["error"]["code"] == "permission_denied"
```

```python
def test_audit_log_redacts_sensitive_args_and_can_filter_events():
    server = AgentBridgeMCPServer(MCPServerConfig(kit_dir=kit, audit_log=audit_log, user="alice", session_id="s1", model="test-model"))
    call_tool(server, "login", {"username": "alice", "password": "secret"})
    events = read_audit_events(audit_log, user="alice", tool="login")
    assert events[0]["args"]["password"] == "<redacted>"
    assert events[0]["user"] == "alice"
```

```python
def test_web_policy_api_loads_and_updates_permissions():
    handler = build_handler(ChatConfig(kit_dir=kit, memory_enabled=False))
    GET /api/policy returns policy and tools
    POST /api/policy writes the updated policy to guardrails/permissions.json
```

- [x] **Step 2: Implement policy defaults**

Add a generated `policy` block to `guardrails/permissions.json`:

```json
{
  "policy": {
    "risk_actions": {
      "read": "allow",
      "write": "confirm",
      "destructive": "deny",
      "external_side_effect": "confirm"
    },
    "confirmation": {
      "external_side_effect": "required",
      "write": "required"
    }
  }
}
```

- [x] **Step 3: Enforce policy in runtime/MCP**

Read kit policy during dry-run and tools/call. `read` is allowed, `write` requires confirmation, `destructive` is denied even when confirmed unless the policy is edited, and `external_side_effect` always requires confirmation.

- [x] **Step 4: Add structured failures**

All blocked runtime outcomes should include:

```json
{
  "error": {
    "code": "permission_denied",
    "message": "Risk level destructive is denied by kit policy.",
    "category": "policy"
  }
}
```

Use codes `permission_denied`, `schema_mismatch`, `http_error`, `timeout`, and `adapter_error` where applicable.

- [x] **Step 5: Add audit redaction and filtering**

Centralize recursive redaction for keys containing `password`, `token`, `secret`, `authorization`, `cookie`, `api_key`, and write JSONL audit entries with `user`, `session_id`, `model`, `tool_call_id`, `confirmation_source`, `risk`, `outcome`, and redacted args.

- [x] **Step 6: Add Web policy view/edit**

Expose `/api/policy` GET/POST and add a policy drawer pane with JSON textarea, reload, and save controls.

### Task 2: Project Understanding and Schema Fidelity

**Files:**
- Modify: `src/agentbridge/models.py`
- Modify: `src/agentbridge/discovery.py`
- Modify: `src/agentbridge/generator.py`
- Modify: `src/agentbridge/agent.py`
- Test: `tests/test_discovery.py`
- Test: `tests/test_generator_runtime.py`
- Add fixture files under temporary test directories only

- [x] **Step 1: Write failing schema tests**

```python
def test_openapi_ref_oneof_nullable_enum_array_and_examples_are_preserved():
    caps = discover_openapi(path, spec)
    schema = caps[0].input_schema
    assert schema["properties"]["status"]["enum"] == ["draft", "published"]
    assert schema["properties"]["tags"]["items"]["type"] == "string"
    assert schema["properties"]["metadata"]["nullable"] is True
```

- [x] **Step 2: Write failing source analysis tests**

```python
def test_ast_scanner_finds_python_routes_and_service_repository_evidence():
    caps = CapabilityDiscoverer().discover([tmp_project])
    assert caps[0].transport["handler"] == "create_chapter"
    assert any(edge["kind"] == "calls" for edge in caps[0].evidence)
```

- [x] **Step 3: Implement `$ref` resolver and schema normalizer**

Resolve local OpenAPI refs from `#/components/schemas/...`, preserve `oneOf`, `anyOf`, `nullable`, `enum`, `format`, `examples`, `items`, `description`, and nested object requirements.

- [x] **Step 4: Add evidence and confidence**

Add optional `evidence` and `confidence` to `Capability.to_dict()` and `from_dict()` while preserving backwards compatibility for older kits.

- [x] **Step 5: Add AST-based source scanners**

Use Python `ast` for route decorators and call edges. Keep existing regex fallback for JavaScript and Java, and add lightweight source evidence for controller/service/repository call chains.

- [x] **Step 6: Generate analysis report**

Write `analysis/report.md` with detected domains, capabilities, risk summary, uncertain items, evidence summary, and AI clarifying questions when available.

- [x] **Step 7: Normalize AI output**

Validate/repair AI capability dictionaries with `Capability.from_dict`, keep invalid AI items as assumptions in agent analysis, and never write malformed capability objects.

### Task 3: Kit Diff, Check, Preservation, and Migration

**Files:**
- Create: `src/agentbridge/diff.py`
- Modify: `src/agentbridge/kit.py`
- Modify: `src/agentbridge/cli.py`
- Modify: `src/agentbridge/generator.py`
- Test: `tests/test_kit_diff.py`
- Test: `tests/test_generator_runtime.py`
- Documentation: `docs/kit-protocol.md`, `docs/kit-protocol.zh-CN.md`, README files

- [x] **Step 1: Write failing diff/check tests**

```python
def test_diff_reports_added_removed_changed_and_risk_changes():
    diff = diff_kits(old_kit, new_kit)
    assert diff["added"] == ["publish_chapter"]
    assert diff["removed"] == ["delete_character"]
    assert diff["risk_changed"][0]["from"] == "read"
```

```python
def test_generate_check_exits_nonzero_when_output_is_stale():
    result = cli.main(["generate", fixture, "-o", existing_kit, "--no-ai", "--check"])
    assert result == 2
```

- [x] **Step 2: Preserve user-authored files on regeneration**

Before writing `prompts/system.md`, `skills/*.md`, and `guardrails/permissions.json`, preserve user edits under `analysis/preserved_user_files.json` and merge policy/tool overrides where possible.

- [x] **Step 3: Add kit migration helpers**

Implement `migrate_kit(kit_dir)` that fills missing additive files and policy fields for older `agentbridge-kit/v1` kits without changing protocol version.

- [x] **Step 4: Generate stronger tool tests**

Expand generated `tests/test_generated_tools.py` so it verifies input schema validity, permission-denied behavior, dry-run never executes, write confirmation, destructive denial, and high-risk confirmation.

- [x] **Step 5: Add CLI commands**

Add:

```bash
agentbridge diff OLD_KIT NEW_KIT
agentbridge generate ... --check
agentbridge validate --migrate
```

`--check` generates into a temporary directory, diffs against output, prints machine-readable summary when `--json` is passed, and returns 2 on differences.

### Task 4: Documentation and Verification

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/architecture.md`
- Modify: `docs/architecture.zh-CN.md`
- Modify: `docs/kit-protocol.md`
- Modify: `docs/kit-protocol.zh-CN.md`
- Modify: `docs/mcp-server.md`
- Modify: `docs/mcp-server.zh-CN.md`
- Modify: `TODO.md`

- [x] **Step 1: Document the new governance flow**

Describe the default human-in-the-loop policy, Web policy editor, structured errors, and audit filtering.

- [x] **Step 2: Document schema/evidence/report fields**

Document `confidence`, `evidence`, `analysis/report.md`, richer schemas, and AI output normalization.

- [x] **Step 3: Document CI commands**

Add `agentbridge diff`, `generate --check`, and migration notes.

- [x] **Step 4: Mark P2/P3/P4 complete**

Only mark current TODO P2/P3/P4 checkboxes complete after tests and compile verification pass.

- [x] **Step 5: Run verification**

```bash
PYTHONPATH=src python -m unittest discover -s tests
PYTHONPATH=src python -m compileall src tests
```
