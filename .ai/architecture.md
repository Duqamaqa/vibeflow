# Vibeflow architecture memory

## Pipeline

User -> OpenAI/Codex CTO -> contract/approval -> semantic router -> isolated worker(s) -> fresh reviewer -> bounded resolver -> deterministic verifier -> done/blocked.

Vibeflow calls FCC at `http://127.0.0.1:8082`. FCC owns credentials, provider protocols, retries, failover, reasoning translation, and provider concurrency. Vibeflow must not duplicate those concerns.

## Context iceberg

Always visible: current contract, this architecture summary, coding rules, relevant decisions, and active files. Retrieve search results, history, and additional files only on demand. Trim low-priority context first. Never dump the repository.

## Routing

Use logical `cheap`, `standard`, and `strong` tiers. Inputs include task type, complexity, risk, scope, uncertainty, verification criticality, and failures. Escalate `cheap -> standard -> strong` at most twice. The 60/30/10 idea is a cost philosophy, not a quota.

Layer 1 uses OpenAI/Codex through a ChatGPT-connected FCC provider when available. There is no Claude dependency and no initial Ollama/local tier.

## Acceptance

Clear low-risk tasks auto-proceed. Material ambiguity or high risk needs approval. A fresh reviewer sees only contract/context/diff/verification. A task is done only after changes are applied and deterministic verification plus independent review are green.

## Guardrails

No provider keys, secret logs, unsafe shell by default, protected-path writes, unrelated dirty-change loss, auto-commit, auto-merge, deploy, or implicit paid benchmark.
