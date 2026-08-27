# Vibeflow AI Coding Architecture v2

## Local dashboard boundary

The `vibeflow web` command serves a loopback-only control room at `127.0.0.1:8765`. The browser is a presentation surface, not an execution authority: it submits validated `plan` or `run` requests to an in-process task service, polls redacted task state, and renders the resulting contract, route, verification, review, changed files, and diff. The existing `AutonomousRunner` remains the only code-change path.

The dashboard never receives provider credentials, never stores data in browser storage, accepts writes only from a matching loopback origin with JSON request bodies, serializes one task per target repository, and cannot bind to a remote interface. It does not introduce commit, push, merge, publish, or deploy capabilities.

## System boundary

```text
USER
  -> Layer-1 OpenAI/Codex CTO
  -> contract + approval policy
  -> semantic/cost router
  -> isolated worker(s)
  -> fresh independent reviewer
  -> bounded resolver
  -> deterministic verifier
  -> final CTO result

Vibeflow -> FCC http://127.0.0.1:8082 -> connected providers
```

Vibeflow owns task analysis, decomposition, context control, semantic routing, tier selection, agent strategy, review isolation, verification policy, telemetry, and budgets. FCC owns provider authentication, request conversion, retries, failover, reasoning-policy translation, and provider-level concurrency. Duplicating FCC behavior in Vibeflow is an architectural defect.

## Control flow

1. Build a contract with goal, constraints, acceptance criteria, non-goals, risk, ambiguity, and failure conditions.
2. Auto-approve only clear low-risk work. Material ambiguity and high risk return `needs-approval` with focused reverse questions.
3. Build a bounded iceberg context from the contract, compact project memory, and active files. Search and history are retrieved on demand.
4. Route to `cheap`, `standard`, or `strong` using deterministic task signals plus an optional Layer-1 override.
5. Create a detached Git worktree when `HEAD` exists, otherwise create an isolated safe copy. Protect every pre-existing dirty path in worktree mode.
6. Run an isolated implementer. Accept JSON file operations only: create/update/delete/rename, repository-relative paths, full UTF-8 content, and SHA-256 preconditions for existing files.
7. Validate traversal, symlink escape, protected paths, secrets, duplicate targets, stale hashes, operation count, and file size before applying inside the isolated workspace.
8. Generate the diff from actual file state and run existing tests/lint/typecheck/build using argv-only subprocesses and timeouts.
9. Instantiate a fresh strong-tier reviewer that sees the contract, bounded context, generated diff, and verification output only.
10. Feed required changes to the resolver, refresh changed-file context, and reapply/reverify/rereview. Cap total resolver iterations at three and tier escalations at two.
11. On PASS, promote final file contents to the target with the original hashes as concurrency preconditions. On failure, roll back/tear down the isolated task state.
12. Return `done` only after reviewed promotion succeeds. Otherwise return `blocked` or `needs-approval`.

## Built-in engines versus prompt skills

Context Iceberg, semantic routing, isolated agent rooms/worktrees, structured change validation, deterministic verification, fresh review, bounded resolution, and hash-guarded safe apply are built-in Vibeflow engines. Users do not import or select these safety mechanisms. Parallel consensus and agent debate are also built in, but the router activates them only for qualifying complex, uncertain, or high-risk decisions.

Prompt skills are different: they are optional, reusable instruction documents stored under `.ai/skills/`. A user can click one or more skill cards for a task, and Vibeflow adds those instructions to bounded model context without rewriting the visible prompt. This keeps the beginner path automatic while giving professional users explicit control over team standards and specialist workflows.

## Core modules

- `contracts.py`: contract validation, approval policy, reverse prompting.
- `router.py`: semantic scoring, tier mapping, escalation caps, CTO override.
- `context.py`: repository-safe retrieval, token estimates, priority trimming.
- `fcc_client.py`: health/models/responses, SSE, structured errors, proxy auth.
- `agent.py`, `worker.py`: executor boundary and strict structured proposal.
- `changes.py`, `workspace.py`, `autonomous.py`: validated file operations, isolation, rollback, and reviewed promotion.
- `reviewer.py`, `resolver.py`: history isolation and bounded correction loop.
- `verifier.py`, `safety.py`: deterministic commands and protected paths.
- `consensus.py`, `debate.py`: independent parallel histories and bounded synthesis.
- `worktrees.py`, `browser.py`, `skills.py`: lower-level worktree and extension boundaries.
- `telemetry.py`, `budget.py`, `benchmark.py`: SQLite evidence and measured tier choice.
- `orchestrator.py`, `cli.py`: Layer-1 flow and concise user commands.

## Model policy

The CTO role is OpenAI/Codex, using ChatGPT subscription access when that account is connected in FCC. Workers should favor inexpensive OpenRouter or NVIDIA models when benchmarks show they are sufficient. Claude is not a dependency. Ollama/local inference is excluded from v1.

The researched August 2026 mappings use DeepSeek V4 Flash 0731 for cheap work, DeepSeek V4 Pro 0813 for standard work, and live FCC discovery for the best available OpenAI/Codex strong model. Published benchmark/provider data selects candidates; local verification and fresh review remain the acceptance gates. The 60/30/10 concept is a target philosophy, never a hard quota.

## Safety invariants

- No provider keys in Vibeflow.
- No entire-repository prompt dumps.
- No reviewer access to implementer reasoning/history.
- No unsafe shell execution by default.
- No writes outside the repository or to protected paths.
- No silent damage to unrelated dirty changes.
- No auto-commit, merge, deploy, or paid benchmark.
- No `done` result based on an unapplied text proposal.

## Project memory

`.ai/architecture.md`, `.ai/decisions.md`, `.ai/coding_rules.md`, and runtime current-task state are compact context inputs. `.ai/routing.toml` maps logical tiers to FCC model slugs. Pricing remains separate, configurable data rather than hardcoded truth.
