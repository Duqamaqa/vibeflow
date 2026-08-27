# Vibeflow v1 implementation status

## Implemented

- Layer-1 contract creation, reverse prompting, approval gates, and final task status.
- Deterministic semantic routing across `cheap`, `standard`, and `strong` logical tiers.
- FCC gateway client for health, model discovery, Responses requests, SSE, request IDs, usage, and structured errors.
- Bounded iceberg context with repository-safe targeted reads, search, Git history, and priority trimming.
- Worker, fresh reviewer, bounded resolver, deterministic verifier, and capped escalation loop.
- DAG task decomposition, isolated agent requests, consensus, bounded debate, and sub-agent verification strategies.
- Optional Git worktree management, lazy skill registry, and browser plugin boundary.
- Protected paths, dirty-tree detection, secret redaction, and blocked commit/deploy actions.
- SQLite telemetry, configurable budget/cost policy, dry-run benchmarks, and measured tier recommendations.
- CLI commands: `init`, `doctor`, `models`, `plan`, `run`, `benchmark`, and `telemetry`/`report`.
- Test doubles for the complete orchestration path and opt-in live FCC access.

## Safe v1 boundaries

- Live workers return change proposals; `done` requires an injected execution adapter to apply a proposal. Vibeflow does not blindly apply model text.
- Worktree creation/removal is implemented, but automatic merging and committing are deliberately prohibited.
- Browser sessions use an injected backend; Chrome automation is not a core dependency.
- Tier slugs and pricing remain configuration until an explicit live benchmark selects them.

## Next milestone

Run representative opt-in benchmarks against connected non-Claude FCC models, select the tier mappings, then implement a reviewed structured-patch adapter if autonomous file mutation is desired.
