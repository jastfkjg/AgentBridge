# AGENTS.md

This file gives coding agents the project context needed to work safely on AgentBridge.

## Project Goal

AgentBridge helps developers add AI assistants to existing systems. It analyzes a target project, discovers business capabilities, and generates a versioned Agent Integration Kit containing tools, skills, prompts, resource schemas, guardrails, dry-run plans, and tests.

The intended workflow is AI-agent-first:

1. Deterministic scanners collect candidate evidence from OpenAPI, GraphQL, SQL, routes, controllers, and services.
2. An AI analysis agent reads the project code and treats scanner output as hints, not final truth.
3. The generator writes a stable `agentbridge-kit/v1` directory protocol.
4. Runtime and dry-run layers enforce guardrails before any host-system side effect.

## Important Files

- `src/agentbridge/discovery.py`: deterministic candidate discovery.
- `src/agentbridge/agent.py`: AI analysis/generation prompts and Claude/Anthropic integration.
- `src/agentbridge/generator.py`: kit protocol writer and tool/skill/guardrail builders.
- `src/agentbridge/runtime.py`: dry-run validation and confirmation checks.
- `src/agentbridge/models.py`: shared dataclasses and kit protocol version.
- `src/agentbridge/policy.py`: risk classification defaults.
- `examples/writing_system/`: fixture system used by tests and docs.
- `tests/`: unit tests for discovery, generation, protocol files, and dry-run behavior.

## Generated Kit Protocol

Generated kits must preserve these stable paths for `agentbridge-kit/v1`:

```text
manifest.json
capabilities.json
analysis/rule_signals.json
analysis/agent_analysis.json
spec/kit-protocol.md
tools/mcp_tools.json
tools/openai_tools.json
tools/claude_tools.json
tools/vercel_ai_tools.ts
skills/*.md
prompts/system.md
resources/schema.json
guardrails/permissions.json
tests/tool_invocation_tests.json
tests/test_generated_tools.py
dry_run_plan.json
```

Additive files are fine. Do not remove or rename required protocol files without bumping `KIT_PROTOCOL_VERSION`.

## Safety Rules

- Destructive and external-side-effect tools must require human confirmation.
- Dry-run must never execute the planned host-system operation.
- AI-generated risk changes must still pass the same guardrail contract tests.
- Treat database-write tools as risky. Prefer HTTP/GraphQL adapters when available.
- Do not put secrets into generated kits, tests, prompts, or examples.

## Development Commands

Use the source layout directly unless testing packaging:

```bash
PYTHONPATH=src python -m unittest discover -s tests
PYTHONPATH=src python -m compileall src tests
PYTHONPATH=src python -m agentbridge discover examples/writing_system
```

Generation requires an AI backend:

```bash
export ANTHROPIC_API_KEY="..."
PYTHONPATH=src python -m agentbridge generate examples/writing_system --output .agentbridge/writing-kit
```

Generated kit self-test:

```bash
python -m unittest discover -s .agentbridge/writing-kit/tests
```

## Coding Guidance

- Keep deterministic discovery conservative. It should produce evidence, not pretend to understand the whole project.
- Put semantic business inference in `agent.py` prompts and analysis normalization.
- Preserve backward compatibility in `runtime.py` where practical.
- Keep tests offline by mocking `AIGenerator`; do not require network or real API keys in unit tests.
- Use standard library features where possible. Optional AI packages belong in `pyproject.toml` extras.

