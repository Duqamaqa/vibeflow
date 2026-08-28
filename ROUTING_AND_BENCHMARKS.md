# Routing and benchmarks

## Logical tiers

`cheap`, `standard`, and `strong` are stable policy names. FCC model slugs are configuration, not architecture. As of August 27, 2026, the project uses internet-researched mappings rather than requiring the user to run a paid benchmark:

- cheap: `nvidia_nim/deepseek-ai/deepseek-v4-flash-0731`;
- standard: `open_router/deepseek/deepseek-v4-pro-0813`;
- strong: `auto:openai-codex`, resolved from the live FCC catalog.
- research: `open_router/google/gemini-3-flash-preview`, used with a bounded online web-search variant through FCC.

The cheap alternative is OpenRouter DeepSeek V4 Flash 0731. The standard alternatives are OpenRouter GLM 5.2 and Kimi K3. Anthropic/Claude inference models are excluded.

## Live research route

Research is separate from the cheap/standard/strong coding escalation ladder. The default is OpenRouter Gemini 3 Flash Preview because FCC currently exposes it, OpenRouter reports native web-search and tool-calling support, and its token price is lower than Gemini 3.5 Flash. Vibeflow caps each research request at 8 results and requires at least one safe public source URL before accepting the report.

OpenRouter recommends its newer `openrouter:web_search` server tool. FCC's current Responses adapter rejects that tool type, so Vibeflow temporarily uses OpenRouter's backward-compatible `:online` route through FCC. This compatibility detail is isolated in `research.py` and should be migrated when FCC exposes server tools. Search usage is paid separately from model tokens.

FCC's current catalog exposes provider models behind an `anthropic/` transport namespace because the gateway presents an Anthropic-compatible adapter. That leading namespace is not the inference vendor: the next segment is `nvidia_nim`, `open_router`, or `openai`. Vibeflow accepts that transport wrapper but rejects every catalog ID containing `claude` or the `~anthropic` model alias. It prefers direct connected-account OpenAI routes over OpenRouter for the strong tier.

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

- [OpenRouter web-search server tool documentation](https://openrouter.ai/docs/guides/features/server-tools/web-search)
- [OpenRouter Gemini 3 Flash Preview pricing and availability](https://openrouter.ai/google/gemini-3-flash-preview)
- [OpenRouter's June 2026 open-weight analysis](https://openrouter.ai/blog/insights/the-open-weight-models-that-matter-june-2026/)
- [Artificial Analysis coding-agent methodology](https://artificialanalysis.ai/agents/coding-agents/)
- [DeepSWE benchmark paper](https://arxiv.org/abs/2607.07946)
- [SWE-Bench Pro paper](https://arxiv.org/abs/2509.16941)
- [NVIDIA NIM model catalog](https://build.nvidia.com/models)

The benchmark command remains available for future audits, but normal use does not require it.
