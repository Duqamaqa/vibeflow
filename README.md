# Vibeflow

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-2ea44f.svg)](LICENSE)
[![Localhost only](https://img.shields.io/badge/Dashboard-localhost%20only-6f42c1)](SECURITY.md)
[![FCC required](https://img.shields.io/badge/Gateway-FCC%20required-f97316)](https://github.com/Alishahryar1/free-claude-code)

**A local control room for safer, more understandable AI coding.** Type a request in your browser, let Vibeflow plan and route the work, then inspect the proposed changes, automated checks, and independent review before the result reaches your project.

> [!IMPORTANT]
> **Vibeflow uses [Free Claude Code (FCC)](https://github.com/Alishahryar1/free-claude-code) as its required local AI gateway. Install and start FCC first, then install Vibeflow.** You do **not** need to clone the FCC source repository. FCC is a separate, independently maintained MIT-licensed project; Vibeflow does not bundle, copy, modify, or redistribute FCC code and is not affiliated with or endorsed by its maintainers.

## Start here

### What you install

| Part | What it does | Where credentials live |
| --- | --- | --- |
| **FCC** | Connects local tools to OpenAI, OpenRouter, NVIDIA NIM, and other model providers. | FCC's local Admin UI and configuration. |
| **Vibeflow** | Plans tasks, chooses a model tier, applies validated changes safely, runs checks, and reviews the result. | Vibeflow does not store provider API keys. |
| **Your project** | The Git repository Vibeflow is allowed to work on. | Your existing files stay in your local project. |

The relationship is simple:

```text
You type a prompt in Vibeflow
          ↓
Vibeflow plans, protects, verifies, and reviews the work
          ↓
FCC sends model requests to the providers you configured
          ↓
The accepted file changes return to your local project
```

### 1. Install FCC first

Use the official FCC installer. You do not need to download or clone its repository.

**macOS or Linux**

```bash
curl -fsSL "https://raw.githubusercontent.com/Alishahryar1/free-claude-code/main/scripts/install.sh" | sh
```

Before running a downloaded script, you may [read the official installer](https://github.com/Alishahryar1/free-claude-code/blob/main/scripts/install.sh). For Windows instructions and current FCC requirements, use the [official FCC Quick Start](https://github.com/Alishahryar1/free-claude-code#quick-start).

Start FCC:

- **macOS:** open **Free Claude Code** from Applications.
- **Linux:** run `fcc-server` and keep that terminal open.

FCC opens its local Admin UI. Connect at least one model provider there. For the default Vibeflow routes, connect ChatGPT under **Providers → Connected accounts** and configure NVIDIA NIM and/or OpenRouter if you want those worker tiers. Provider availability, prices, subscriptions, and usage rules are controlled by those providers and may change.

> [!CAUTION]
> Put provider keys and connected-account credentials only in FCC's local Admin UI. Never paste keys into the Vibeflow repository, a prompt, a GitHub issue, a screenshot, or a commit.

### 2. Install Vibeflow

Install the latest public version directly from GitHub:

```bash
uv tool install git+https://github.com/Duqamaqa/vibeflow.git
vibeflow --help
```

This creates an isolated user-local command. It does not modify system Python. The FCC installer normally provides or verifies `uv`; otherwise install it from the [official uv documentation](https://docs.astral.sh/uv/getting-started/installation/).

### 3. Prepare a project once

Open the project you want Vibeflow to edit, then create its safe local configuration:

```bash
cd /absolute/path/to/your-project
vibeflow init
vibeflow doctor
```

`init` adds a small `.ai/` configuration area without overwriting existing files. Review and commit those non-secret project instructions if you want your team to share them. `doctor` checks Git, FCC, model availability, and discovered verification commands.

If `doctor` reports that no test command was discovered, Vibeflow itself may still be installed correctly; it means the target project does not yet expose a deterministic test command that Vibeflow can trust.

### 4. Open the dashboard

```bash
vibeflow web --repo /absolute/path/to/your-project
```

Your browser opens at **http://127.0.0.1:8765**. Type your request in the large **New task** box.

- Click **Browse…** beside **Target repository** to open the native folder chooser. On macOS this opens Finder. Choose an existing Git repository or a project folder that you want Vibeflow to prepare.
- If the selected folder is not ready, use the prominent **Prepare this folder** action before writing a task. With one explicit click, Vibeflow creates local Git tracking when missing plus its `.ai/` configuration. It does not alter existing project files, create a commit, or push anything.
- **Plan only** and **Run safely** stay blocked until the selected folder has the minimum safe setup, so a missing `.ai/routing.toml` cannot become a confusing failed task.
- **Plan only** explains the contract and route without model inference or file changes.
- **Run safely** starts the complete coding pipeline.
- **Approve flagged high-risk scope** approves that task's reviewed scope only. It never approves a commit, push, merge, publish, or deployment.

Keep the terminal window open while using Vibeflow. Press `Ctrl+C` to stop it.

### After restarting your computer

1. Start **Free Claude Code** or run `fcc-server`.
2. Start Vibeflow for the project you want to work on:

```bash
vibeflow web --repo /absolute/path/to/your-project
```

3. Open **http://127.0.0.1:8765** if the browser does not open automatically.

## How Vibeflow works — without the jargon

Vibeflow is the manager and safety layer; FCC is the connection to AI models.

1. **Understands the request.** Vibeflow turns your prompt into a clear goal, boundaries, and success checklist.
2. **Stops on risky ambiguity.** Sensitive or unclear work can return `needs-approval` before any model changes files.
3. **Chooses the right model.** Simple work starts with a lower-cost worker; difficult work can use stronger models.
4. **Limits what the model sees.** The worker receives relevant project context instead of an uncontrolled copy of everything.
5. **Accepts only structured changes.** The worker must propose validated create, update, delete, or rename operations—not arbitrary shell instructions.
6. **Works in isolation.** Vibeflow prepares changes away from your main working copy and protects unrelated uncommitted work.
7. **Checks the result.** It creates the real Git diff and runs discovered tests, linting, type checks, and builds.
8. **Uses a fresh reviewer.** A separate strong model sees the task, relevant context, diff, and check results—not the worker's private reasoning.
9. **Repairs within limits.** Failed review can trigger a bounded repair-and-review loop. It cannot retry forever.
10. **Returns evidence.** The final state is `done`, `blocked`, or `needs-approval`, with changed files and verification results.

Vibeflow never automatically commits, pushes, merges, publishes, deploys, or edits protected credential and policy paths.

## Skills: from beginner to professional

The dashboard separates **built-in Vibeflow engines** from **optional prompt skills**. This matters because Context Iceberg, isolated agent rooms, model routing, structured changes, verification, fresh review, bounded resolution, and safe apply are part of Vibeflow itself. You do not import or check them; safety engines are always on, while parallel consensus and agent debate activate automatically for qualifying difficult tasks.

To explicitly request a multi-agent strategy, say `use parallel agents` or `use agent debate` in the prompt. Vibeflow treats those as high-uncertainty work; debate is high-risk and therefore requires approval. Otherwise the semantic router turns these modes on only when the task language signals both difficulty and uncertainty.

You do not need optional skills to use Vibeflow. A beginner can choose a repository, write the desired outcome, and use the default safe route. Prompt skills become useful when you want Vibeflow to follow a repeatable personal or team standard.

In the dashboard's **Skills** section you can:

- **Create a skill** with a name, plain-language description, trigger phrases, risk level, and reusable instructions.
- **Import skill folder** and choose an existing folder with the native folder chooser. The folder must contain `SKILL.md`.
- Click or check one or more skill cards for the next plan or autonomous task. Vibeflow adds their full instructions to bounded model context without rewriting the visible prompt.
- Remove a repository skill without affecting your original imported folder.

Skills are stored per project under `.ai/skills/<skill-name>/SKILL.md`, so a professional team can review and commit them like other project instructions. Trigger phrases may select a skill automatically; explicitly checked skills are always included in that task's bounded context.

For supply-chain safety, Vibeflow imports **only** the UTF-8 `SKILL.md` instruction document. It does not copy or execute scripts, binaries, hooks, or other files from the source folder. Symlinks, oversized documents, malformed metadata, and credential-like content are rejected. High-risk skills raise the task risk and require approval.

Minimal compatible skill format:

```markdown
---
name: frontend-accessibility
description: Apply our accessible interface standard
triggers:
  - accessibility
  - frontend
risk: low
---

Require visible focus states, semantic labels, keyboard navigation, and regression tests.
```

## FCC, ownership, and rights

Vibeflow deliberately keeps FCC outside this repository:

- FCC is a **required runtime dependency** and runs as a separate local process, normally at `127.0.0.1:8082`.
- Vibeflow communicates with FCC through its local HTTP interface.
- Vibeflow does not include FCC source code, binaries, installers, branding, or credentials.
- Vibeflow does not install, update, configure, or manage FCC automatically.
- FCC is published separately under the [MIT License](https://github.com/Alishahryar1/free-claude-code/blob/main/LICENSE). Its maintainers control its code, releases, documentation, name, and support.
- Vibeflow is also MIT-licensed, but its license covers Vibeflow only—not FCC, model providers, coding agents, or their services.
- Vibeflow is not affiliated with or endorsed by FCC, Anthropic, OpenAI, OpenRouter, NVIDIA, or other providers. Their names and trademarks belong to their respective owners.

This separation is intentional: it makes installation and ownership clearer and avoids presenting third-party code as part of Vibeflow. Users remain responsible for reviewing FCC and provider terms, choosing permitted integrations, and protecting their accounts. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [SECURITY.md](SECURITY.md).

## Security and privacy

- The dashboard binds to `127.0.0.1` and refuses public network binding.
- Repository and skill folder pickers run through the local backend; the browser never receives general filesystem access.
- It does not store credentials in cookies, URLs, `localStorage`, or `sessionStorage`.
- Provider credentials remain in FCC's local configuration.
- Prompts and task evidence are redacted before local task-state persistence.
- File operations reject path traversal, symlink escape, `.git` internals, secrets, protected paths, and configured denylist matches.
- Pre-existing dirty files are protected from autonomous overwrites.
- Imported skills are instruction-only, secret-scanned, size-limited, and protected from autonomous modification.
- `.env`, private keys, credentials, local task state, and common editor artifacts are excluded by `.gitignore`.

Localhost is not a substitute for operating-system security. Do not expose the dashboard through a tunnel, reverse proxy, shared container, or remote bind. Read [SECURITY.md](SECURITY.md) before connecting providers or publishing a fork.

## Other ways to enter prompts

The browser dashboard is the recommended interface. The command line is also available.

**Interactive session**

```bash
cd /absolute/path/to/your-project
vibeflow chat
```

Type requests after the `vibeflow>` prompt. Use `/plan <request>`, `/status`, and `/quit` inside the session.

**One task**

```bash
vibeflow run --repo /absolute/path/to/your-project "Fix the failing parser tests without changing the public API"
```

**Plan without changes**

```bash
vibeflow plan --repo /absolute/path/to/your-project "Refactor the cache layer"
```

## Command reference

| Command | Purpose |
| --- | --- |
| `vibeflow init` | Create missing project configuration without overwriting existing files. |
| `vibeflow doctor` | Check Python, Git, FCC, models, and verification tools. |
| `vibeflow models --live` | Show configured tiers and models currently exposed by FCC. |
| `vibeflow web` / `vibeflow ui` | Start the loopback-only browser dashboard. |
| `vibeflow chat` | Start an interactive prompt session. |
| `vibeflow plan` | Build the contract, route, context, skills, and task graph without inference. |
| `vibeflow run` | Run one autonomous task; add `--dry-run` for a no-inference preflight. |
| `vibeflow status` | Show the latest task outcome. |
| `vibeflow telemetry report` | Show aggregate request telemetry. |
| `vibeflow benchmark` | Prepare benchmark work; live paid calls require explicit flags. |

Use `--no-open` to open the dashboard link yourself or `--port 9000` if port 8765 is busy. Vibeflow currently targets macOS and Linux with Python 3.11+, Git, and `uv`.

## Technical pipeline

```text
prompt
  → contract and approval policy
  → bounded repository context
  → semantic cheap / standard / strong routing
  → FCC-backed worker
  → validated structured change proposal
  → isolated Git worktree or safe copy
  → generated Git diff
  → deterministic verification
  → fresh strong-tier review
  → bounded resolver loop when needed
  → hash-guarded promotion
  → done / blocked / needs-approval
```

Clear low-risk tasks may proceed automatically. Material ambiguity, production deployment, authentication or authorization, payments, credentials, destructive data changes, and database or schema migrations require approval. A blocked task rolls back its isolated task changes. Approval never grants permission to commit, push, merge, publish, or deploy.

## Model routing

The checked-in defaults selected on August 27, 2026 are:

- **Cheap:** `nvidia_nim/deepseek-ai/deepseek-v4-flash-0731`
- **Standard:** `open_router/deepseek/deepseek-v4-pro-0813`
- **Strong:** `auto:openai-codex`

The strong route discovers the best compatible OpenAI/Codex model exposed by the user's FCC instance instead of hardcoding an account-dependent ID. FCC may expose provider models through an Anthropic-compatible transport prefix; Vibeflow treats that as protocol routing and rejects Claude/Anthropic inference model IDs from the selected pipeline.

Model availability, quality, prices, and provider policies change. Published benchmarks inform the defaults, but deterministic checks and independent review—not a benchmark score—decide whether a task is accepted. See [ROUTING_AND_BENCHMARKS.md](ROUTING_AND_BENCHMARKS.md).

## Development install

Contributors who want a source checkout can use:

```bash
git clone https://github.com/Duqamaqa/vibeflow.git
cd vibeflow
uv tool install --editable .
vibeflow --help
```

Run the tests without paid inference:

```bash
PYTHONPATH=src python3 -W error -m unittest discover -s tests -v
python3 -m compileall -q src tests
```

Live FCC health and model-catalog tests skip cleanly when FCC is unavailable. Ordinary unit tests use fakes and do not make paid inference calls.

## Project documentation

- [Security model and secret-handling rules](SECURITY.md)
- [Contributor guide](CONTRIBUTING.md)
- [Third-party ownership and attribution](THIRD_PARTY_NOTICES.md)
- [Routing choices and benchmark research](ROUTING_AND_BENCHMARKS.md)
- [Detailed system architecture](AI_Coding_Architecture_v2.md)
- [MIT License](LICENSE)

## License

Vibeflow is released under the [MIT License](LICENSE). Third-party projects and services retain their own licenses, terms, and trademarks.
