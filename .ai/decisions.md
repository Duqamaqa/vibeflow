# Architecture decisions

## ADR-001: FCC is the provider gateway

Vibeflow owns semantic routing and orchestration. FCC owns credentials, provider protocol adaptation, retries, fallback/failover, reasoning translation, and provider concurrency. Vibeflow stores no provider keys.

## ADR-002: OpenAI is Layer 1

The CTO/orchestrator role is OpenAI/Codex through ChatGPT subscription access when FCC exposes a connected `openai/` model. There is no Claude dependency. OpenRouter and NVIDIA models are preferred for inexpensive workers. Local Ollama models are excluded from v1 unless later benchmarks prove a material advantage.

## ADR-003: Evidence before acceptance

Every implementation passes a fresh reviewer and deterministic verification. Reviewer inputs exclude implementer reasoning and history. A text proposal is not considered applied work.

## ADR-004: Context is retrieved, not dumped

Contracts, architecture, coding rules, relevant decisions, and active files form the visible iceberg. Repository search, history, and additional files are retrieved only on demand and trimmed by priority.

## ADR-005: Measurement is local and configurable

Telemetry uses SQLite. Price estimates use a user-maintained table and remain unknown when pricing is absent. Paid benchmarks are always opt-in.
