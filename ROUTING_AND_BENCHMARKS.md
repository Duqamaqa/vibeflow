# Routing and benchmarks

## Logical tiers

`cheap`, `standard`, and `strong` are stable policy names. FCC model slugs are configuration, not architecture. As of August 27, 2026, the project uses internet-researched mappings rather than requiring the user to run a paid benchmark:

- cheap: `openrouter/deepseek/deepseek-v4-flash-0731`;
- standard: `openrouter/deepseek/deepseek-v4-pro-0813`;
- strong: `auto:openai-codex`, resolved from the live FCC catalog.

The standard alternatives are OpenRouter GLM 5.2 and NVIDIA NIM DeepSeek V4 Pro 0813. Anthropic/Claude is excluded.

The deterministic base score considers task type, complexity, risk, scope, uncertainty, verification criticality, and previous failures. Layer 1 may override a tier explicitly. A failure or material uncertainty moves up one tier, capped at two escalations.

The 60/30/10 target means most bounded tasks should be cheap, fewer should need standard, and only genuinely hard/high-risk work should start strong. Vibeflow does not manipulate decisions to hit a quota.

## Evidence and caveats

Benchmark configuration and dry-run discovery are free. Live benchmark inference must require an explicit opt-in command. Representative cases should cover:

- focused edits with deterministic tests;
- medium implementation tasks with reviewer scoring;
- debugging and architecture decisions with uncertainty scoring;
- failure recovery and escalation.

Record model slug, inferred provider, logical tier, test result, reviewer score, latency, input/output/cache tokens, and estimated cost. Prices belong in a user-maintained pricing table; empty or missing prices mean cost is unknown, never zero.

The selection cross-checks current OpenRouter availability/pricing, Artificial Analysis' composite benchmark, NVIDIA NIM availability, and official OpenAI model guidance. Public benchmark scores are harness-sensitive and can be contaminated or saturated, so they select candidates but do not waive local deterministic verification.

Current sources:

- [OpenRouter's June 2026 open-weight analysis](https://openrouter.ai/blog/insights/the-open-weight-models-that-matter-june-2026/)
- [Artificial Analysis coding-agent methodology](https://artificialanalysis.ai/agents/coding-agents/)
- [DeepSWE benchmark paper](https://arxiv.org/abs/2607.07946)
- [SWE-Bench Pro paper](https://arxiv.org/abs/2509.16941)
- [NVIDIA NIM DeepSeek V4 Pro 0813](https://build.nvidia.com/deepseek-ai/deepseek-v4-pro-0813)

The benchmark command remains available for future audits, but normal use does not require it.
