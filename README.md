# Vibeflow

Vibeflow is a dependency-light Python orchestrator for AI coding work. It owns task contracts, semantic routing, bounded context, independent review, deterministic verification, multi-agent strategies, telemetry, and budget policy. Free Claude Code (FCC) remains the provider gateway.

## Local dashboard

Vibeflow includes a private coding control room at **http://127.0.0.1:8765**. This is where you type prompts, choose a target repository, preview a plan, run autonomous work, and inspect the route, changed files, deterministic checks, reviewer decision, and diff.

### First setup

Requirements: macOS or Linux, Python 3.11+, [uv](https://docs.astral.sh/uv/), Git, and a running FCC gateway.

```bash
git clone https://github.com/Duqamaqa/vibeflow.git
cd vibeflow
uv tool install --editable .
vibeflow --help
```

`uv tool install` creates an isolated user-local command. It does not modify system Python.

### Start after a computer restart

Start FCC first, then run this command with the repository you want Vibeflow to edit:

```bash
vibeflow web --repo /absolute/path/to/your-project
```

Your browser opens automatically at **http://127.0.0.1:8765**. Keep the terminal window open while you use Vibeflow. Press `Ctrl+C` to stop it.

If you do not want the user-local command, run directly from a clone instead:

```bash
uv run --project /absolute/path/to/vibeflow vibeflow web --repo /absolute/path/to/your-project
```

Use `--no-open` when you want to open the link yourself. Use `--port 9000` if port 8765 is already occupied. Vibeflow refuses non-localhost binding.

### Where prompts go

Type natural-language coding requests in the large **New task** box:

- **Plan only** builds the contract and route without inference or file changes.
- **Run safely** starts the complete autonomous pipeline.
- **Approve flagged high-risk scope** approves only the reviewed contract; it never authorizes commit, push, merge, publish, or deploy.
- Change **Target repository** to point the same running dashboard at another local project.

The dashboard does not store API keys, tokens, prompts, or repository data in browser storage. Credentials remain in FCC's local connected-account configuration. See [SECURITY.md](SECURITY.md) before connecting providers or publishing a fork.

## Responsibility boundary

Vibeflow never stores provider keys and does not reimplement provider authentication, protocol conversion, provider retries, fallback/failover, reasoning translation, or provider concurrency. FCC owns those concerns. Vibeflow talks only to FCC's local HTTP interface.

```text
User
  -> Vibeflow CTO contract and router
  -> isolated worker(s)
  -> fresh reviewer
  -> resolver and deterministic verifier
  -> final done / blocked / needs-approval result

Vibeflow -> FCC localhost gateway -> connected OpenAI / OpenRouter / NVIDIA providers
```

## How I use Vibeflow every day

Requirements: Python 3.11 or newer, `uv`, Git, and FCC running at `127.0.0.1:8082`.

The supported no-global-install command is `uv run`. From the Vibeflow source repository:

```bash
uv run vibeflow --help
uv run python -m vibeflow --help
uv run vibeflow doctor
uv run vibeflow models --live
uv run vibeflow web --repo /path/to/my-project
```

To make `vibeflow` available as a short user-local command everywhere, optionally run this once. This uses `uv`'s isolated tool environment and does not modify system Python:

```bash
uv tool install --editable .
vibeflow --help
```

### Where I type prompts

For an ongoing session, enter the target project and start the REPL. Type each natural-language coding request at the `vibeflow>` prompt:

```bash
cd /path/to/my-project
uv run --project /path/to/vibeflow vibeflow chat
```

For one task, put the prompt directly after `run`:

```bash
cd /path/to/my-project
uv run --project /path/to/vibeflow vibeflow run "Fix the failing parser tests and keep the public API compatible"
```

The current directory is the target repository. Use `--repo` when you do not want to change directories:

```bash
uv run --project /path/to/vibeflow vibeflow run --repo /path/to/my-project "Add request validation and tests"
```

Use `plan` for contract/route/context only, or `run --dry-run` for a no-inference preflight:

```bash
uv run --project /path/to/vibeflow vibeflow plan "Refactor the cache layer"
uv run --project /path/to/vibeflow vibeflow run --dry-run "Refactor the cache layer"
uv run --project /path/to/vibeflow vibeflow status
```

Ordinary tests never make inference calls:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
```

## FCC and OpenAI subscription access

FCC 5.13.10 supports an `openai/<model-id>` provider backed by a connected ChatGPT account. In FCC Admin, open **Providers -> Connected accounts**, connect ChatGPT, then restart any already-running agent. Headless systems can use FCC's device-code flow. This connection lives in FCC, not Vibeflow.

Official OpenAI documentation distinguishes ChatGPT subscription sign-in from API-key usage. Codex local clients can sign in with ChatGPT for subscription access; API-key usage is separately billed. See [OpenAI authentication](https://learn.chatgpt.com/docs/auth).

Vibeflow does not read `~/.codex/auth.json`, `~/.fcc`, or provider environment variables. If FCC proxy authentication is enabled, pass only the FCC proxy bearer token to Vibeflow through its own runtime option or environment; never copy provider keys into this project.

## What is automatic

- Clear low-risk tasks auto-proceed; material ambiguity or high risk stops for approval.
- Routing uses logical `cheap`, `standard`, and `strong` tiers. The 60/30/10 idea is a cost philosophy, not a quota.
- Failures and uncertainty escalate at most twice: `cheap -> standard -> strong`.
- Reviewers receive only the contract, bounded context, diff, and verification output.
- Workers must return validated create/update/delete/rename operations with SHA-256 preconditions. Arbitrary shell prose and worker-claimed diffs fail closed.
- Changes run in a detached Git worktree when the target has a commit, otherwise in an isolated safe copy. Pre-existing dirty paths are protected and unrelated dirty files are preserved.
- Vibeflow generates the diff itself, runs discovered tests/lint/typecheck/build, and sends only contract, bounded context, diff, and verification evidence to a fresh reviewer.
- Reviewer failure triggers at most three total resolver iterations and at most two model-tier escalations. A blocked task rolls back the isolated task changes.
- A proposal is not `done` until deterministic checks and the fresh strong-tier review pass and hash-guarded promotion to the target repository succeeds.
- Consensus and debate are reserved for high-value, high-uncertainty decisions.
- No command commits, pushes, merges, deploys, publishes, or changes protected policy/secret paths.

## What still requires approval

- Deterministically flagged high-risk work such as production deployment, authentication/authorization, payments, credentials, destructive data changes, and database/schema migrations returns `needs-approval`.
- Review the generated plan, then re-run the same command with `--approve` if the scope is correct.
- External writes, commits, pushes, merges, deployment, and publishing are never implied by `--approve`; request those separately outside the autonomous apply pipeline.

## CLI behavior

- `web`/`ui` starts the loopback-only dashboard and opens the browser.
- `init` creates missing `.ai` memory/config files and never overwrites existing ones.
- `doctor` checks Python, Git state, discovered verification commands, and FCC health.
- `models --live` reads FCC model metadata without making an inference request.
- `chat` is the prompt REPL. `/plan <request>`, `/status`, and `/quit` are available inside it.
- `plan` builds the real local contract, route, context, skills, and task graph without inference.
- `run` is the one-shot autonomous path. `--dry-run` disables inference and changes.
- `benchmark` is dry by default; `--live`/`--allow-paid` is the only paid-call gate.
- `status` reads the most recent task outcome; `telemetry report` reads aggregate request telemetry.

## Model tiers selected August 27, 2026

- `cheap`: `nvidia_nim/deepseek-ai/deepseek-v4-flash-0731`. The live FCC catalog exposes this NVIDIA NIM route. OpenRouter lists the same underlying model at $0.035/M input and $0.28/M output with 29 providers, while Artificial Analysis scores the max-effort variant 52 and reports 133.1 output tokens/second. It is the mechanical-edit/default-cost tier.
- `standard`: `open_router/deepseek/deepseek-v4-pro-0813`. The live FCC catalog exposes this exact OpenRouter route. OpenRouter lists the GA model at $0.66/M input and $1.98/M output with 15 providers. Artificial Analysis scores it 53 and places it among the leading open-weight models.
- `strong`: `auto:openai-codex`. Vibeflow reads FCC `/v1/models` and selects the highest-ranked available OpenAI/Codex model instead of hardcoding an account-dependent slug. Official OpenAI documentation currently recommends GPT-5.6 Sol for complex reasoning and coding; GPT-5.3-Codex remains a specialized agentic-coding model. Catalog visibility still does not prove the connected account can execute, so `doctor` requires FCC health and valid tier resolution before work starts.

FCC currently prefixes these live IDs with `anthropic/` because it exposes them through an Anthropic-compatible transport adapter. That prefix is protocol routing, not the model vendor. Vibeflow permits the wrapper only for non-Anthropic providers and rejects every catalog ID containing `claude` or the `~anthropic` model alias. The selected inference models remain DeepSeek and OpenAI.

Sources: [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model), [GPT-5.3-Codex](https://developers.openai.com/api/docs/models/gpt-5.3-codex), [OpenRouter DeepSeek V4 Flash 0731](https://openrouter.ai/deepseek/deepseek-v4-flash-0731), [OpenRouter DeepSeek V4 Pro 0813](https://openrouter.ai/deepseek/deepseek-v4-pro-0813), [Artificial Analysis V4 Flash](https://artificialanalysis.ai/models/deepseek-v4-flash), [Artificial Analysis V4 Pro](https://artificialanalysis.ai/models/deepseek-v4-pro), and [NVIDIA NIM models](https://build.nvidia.com/models).

No Anthropic/Claude inference model appears in the selected pipeline. Published benchmarks are cross-checks, not proof for every repository; deterministic project verification and independent review remain the acceptance gates.

See `AI_Coding_Architecture_v2.md` and `ROUTING_AND_BENCHMARKS.md` for details.
