"""Create deterministic per-repository Vibeflow configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .safety import SafetyGuard


INIT_CONFIG = """version = 1
default_mode = "autonomous"
allow_auto_commit = false
allow_auto_deploy = false

[verification]
timeout_seconds = 60

[benchmark]
live = false
"""

INIT_MEMORY_FILES: Mapping[str, str] = {
    ".ai/architecture.md": "# Architecture\n\nDescribe stable system boundaries here.\n",
    ".ai/decisions.md": "# Decisions\n\nRecord durable architecture decisions here.\n",
    ".ai/coding_rules.md": "# Coding rules\n\n- Preserve unrelated changes.\n- Require deterministic verification.\n",
    ".ai/routing.toml": (
        "[tiers.cheap]\nmodel = \"open_router/deepseek/deepseek-v4-flash-0731\"\n\n"
        "[tiers.standard]\nmodel = \"open_router/deepseek/deepseek-v4-pro-0813\"\n\n"
        "[tiers.strong]\nmodel = \"auto:openai-codex\"\n\n"
        "[research]\n"
        "model = \"open_router/google/gemini-3-flash-preview\"\n"
        "max_results = 8\n\n"
        "[policy]\nmax_escalations = 2\n\n"
        "[candidates]\n"
        "cheap = [\"nvidia_nim/deepseek-ai/deepseek-v4-flash-0731\"]\n"
        "standard = [\"open_router/z-ai/glm-5.2\", \"open_router/moonshotai/kimi-k3\"]\n"
        "strong = []\n"
    ),
}


def initialize_repository(
    repo_root: str | Path,
    *,
    config_path: str = ".ai/vibeflow.toml",
) -> tuple[Path, tuple[Path, ...]]:
    """Create missing project files without overwriting existing content."""

    root = Path(repo_root).expanduser().resolve()
    guard = SafetyGuard(root)
    validated_config = guard.validate_path(config_path, allow_protected=True)
    templates = {config_path: INIT_CONFIG, **INIT_MEMORY_FILES}
    created: list[Path] = []
    for relative_path, content in templates.items():
        path = guard.validate_path(relative_path, allow_protected=True)
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created.append(path)
    return validated_config, tuple(created)
