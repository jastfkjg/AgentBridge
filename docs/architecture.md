# Architecture

AgentBridge is organized around an AI-agent-first generation pipeline.

## Pipeline

1. Candidate discovery scans schemas, routes, and database definitions.
2. The AI analysis agent reads project code and candidate evidence.
3. The AI agent produces project analysis, risk reasoning, enhanced capabilities, skills, and prompts.
4. The generator writes the `agentbridge-kit/v1` protocol directory.
5. Runtime tools use guardrails and dry-run validation before executing host-system adapters.

## Why Rules Still Exist

Rules are useful for cheap, deterministic evidence collection. They should not be treated as the final business model. The AI analysis layer is responsible for understanding controller/service behavior, workflow intent, side effects, and missing operations implied by code.

## Safety Boundary

Generation may infer tools, but runtime execution must obey `guardrails/permissions.json`. A generated assistant should never perform destructive or external-side-effect actions without explicit human confirmation.

