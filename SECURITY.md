# Vibeflow security

Vibeflow is designed to make local coding autonomy observable without turning a browser tab or Git repository into a credential store.

## Credential boundary

- Vibeflow does not require provider API keys in this repository.
- Provider credentials and connected ChatGPT accounts belong in FCC's local configuration and administration interface.
- Never paste API keys, bearer tokens, private keys, or account exports into `.ai/`, the dashboard prompt, source files, tests, issues, commits, or screenshots.
- The dashboard does not use `localStorage`, `sessionStorage`, cookies, or URL parameters for credentials.
- If FCC proxy authentication is enabled, provide that token only at runtime outside the repository. Never hardcode it.

## Dashboard boundary

- The server binds to `127.0.0.1` by default and rejects non-loopback binds.
- Browser writes require JSON and a matching loopback origin.
- Responses set a restrictive Content Security Policy, block framing, disable referrers, and prevent MIME sniffing.
- Request bodies and prompts have deterministic size limits.
- Task results are redacted before they reach the browser or `.vibeflow/last-task.json`.
- `.vibeflow/` is ignored by Git.

Localhost is not a substitute for operating-system security. Do not run Vibeflow on an untrusted computer or expose the port through a tunnel, reverse proxy, shared container, or remote bind.

## Repository boundary

The autonomous apply layer rejects path traversal, symlink escape, `.git` internals, `.env` files, private keys, credential-like files, routing policy, state files, and configured denylist matches. It protects pre-existing dirty paths and applies changes in an isolated Git worktree or safe copy before hash-guarded promotion.

Vibeflow never commits, pushes, merges, publishes, deploys, or modifies an external repository unless that separate action is explicitly requested and performed outside the autonomous runner.

## Native folder and skill boundary

- The dashboard asks its loopback-only Python backend to open the operating system's native folder chooser. Web pages never receive unrestricted filesystem access.
- Repository selection accepts local directories, then reports separately whether Git and Vibeflow configuration are ready.
- Repository setup creates missing `.ai/` files without overwriting existing content. For a non-Git project folder, the dashboard can run fixed-argument `git init` only after the user explicitly clicks **Prepare this folder**; it creates no commit, remote, push, hook, or network request.
- Skill imports accept a folder containing `SKILL.md`, read only that instruction document, and do not copy or execute scripts, hooks, binaries, or support files.
- Skill documents must be UTF-8, remain within deterministic size limits, contain valid metadata, avoid symlinks, and pass credential-pattern checks.
- Installed `.ai/skills/**` paths are protected from autonomous worker changes. Removing a skill is a separate, explicit dashboard action.
- A high-risk selected skill raises the task's contract risk and therefore requires explicit approval.

## Before publishing a fork

Run all of these from the repository root:

```bash
git status --short
git diff --cached
git ls-files
rg -n --hidden --glob '!.git/**' --glob '!uv.lock' '(sk-(proj-)?[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----)'
```

Also inspect ignored and untracked files manually. A scanner reduces risk; it does not replace reviewing the exact staged diff.

## Reporting a vulnerability

Do not open a public issue containing a working exploit, secret, private repository path, or sensitive log. Use GitHub's private vulnerability reporting feature when enabled, or contact the repository owner privately.
