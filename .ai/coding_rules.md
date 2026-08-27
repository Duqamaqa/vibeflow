# Coding rules

- Support Python 3.11+ and prefer the standard library.
- Keep FCC and Vibeflow source/configuration separate.
- Never store or log provider credentials, auth caches, or raw secrets.
- Use argv-based subprocess calls with explicit working directories and timeouts.
- Resolve and validate repository-relative paths before reading or writing.
- Preserve unrelated dirty changes; never auto-commit, auto-merge, or deploy.
- Keep implementer and reviewer histories isolated.
- Require explicit acceptance criteria and deterministic evidence.
- Add focused `unittest` coverage for behavior changes.
- Ordinary tests and dry runs must not make inference calls.
