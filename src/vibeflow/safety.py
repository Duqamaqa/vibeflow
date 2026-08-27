"""Deterministic repository safety checks for Vibeflow tooling."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
from typing import Any, Callable, Mapping, Sequence


REDACTED = "[REDACTED]"

DEFAULT_PROTECTED_PATTERNS: tuple[str, ...] = (
    ".git",
    ".git/**",
    ".vibeflow",
    ".vibeflow/**",
    ".ai/state.json",
    ".ai/routing.toml",
    ".ai/vibeflow.toml",
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "**/*.key",
    "**/*.pem",
    "**/*.p12",
    "**/*.pfx",
    "**/id_rsa*",
    "**/id_ed25519*",
    "**/*credentials*",
    "**/*secret*",
    "**/terraform.tfstate",
    "**/terraform.tfstate.*",
)

_SECRET_KEY_CORE = (
    r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|authorization|"
    r"client[_-]?secret|private[_-]?key|password|passwd|secret)"
)
_SECRET_KEY_PATTERN = (
    rf"(?:[A-Za-z0-9]+[_-])*{_SECRET_KEY_CORE}(?:[_-][A-Za-z0-9]+)*"
)
_SECRET_KEY_NAME = re.compile(_SECRET_KEY_PATTERN, re.IGNORECASE)
_JSON_SECRET = re.compile(
    rf'(?P<prefix>"{_SECRET_KEY_PATTERN}"\s*:\s*")'
    r'(?P<value>[^"]*)(?P<suffix>")',
    re.IGNORECASE,
)
_ASSIGNED_SECRET = re.compile(
    rf"(?P<prefix>\b{_SECRET_KEY_PATTERN}\b\s*[:=]\s*)"
    r"""(?P<quote>['"]?)(?P<value>[^\s,;'"]+)(?P=quote)""",
    re.IGNORECASE,
)
_AUTH_TOKEN = re.compile(r"(?i)(\b(?:Bearer|Basic)\s+)[A-Za-z0-9._~+/=-]+")
_KNOWN_TOKEN = re.compile(
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|"
    r"gh[pousr]_[A-Za-z0-9_]{12,}|"
    r"xox[baprs]-[A-Za-z0-9-]{12,}|"
    r"AKIA[0-9A-Z]{16}|"
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b"
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?"
    r"-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_URL_CREDENTIALS = re.compile(
    r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)(?P<credentials>[^/@\s]+:[^/@\s]+)@",
    re.IGNORECASE,
)


class SafetyViolation(ValueError):
    """Raised when an operation crosses a deterministic safety boundary."""


@dataclass(frozen=True)
class DirtyEntry:
    """One path reported by ``git status --porcelain``."""

    status: str
    path: str
    original_path: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "status": self.status,
            "path": self.path,
            "original_path": self.original_path,
        }


@dataclass(frozen=True)
class DirtyState:
    """Read-only repository state; no stash, reset, or cleanup is attempted."""

    available: bool
    is_repository: bool
    dirty: bool
    entries: tuple[DirtyEntry, ...] = ()
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "is_repository": self.is_repository,
            "dirty": self.dirty,
            "entries": [entry.to_dict() for entry in self.entries],
            "error": self.error,
        }


def _matches_protected_pattern(relative_path: str, pattern: str) -> bool:
    normalized = relative_path.strip("/")
    normalized_pattern = pattern.strip("/")
    if not normalized_pattern:
        return False
    if normalized == normalized_pattern.removesuffix("/**"):
        return True
    path = PurePosixPath(normalized)
    if path.match(normalized_pattern) or fnmatchcase(normalized, normalized_pattern):
        return True
    if normalized_pattern.startswith("**/"):
        short_pattern = normalized_pattern[3:]
        return path.match(short_pattern) or fnmatchcase(normalized, short_pattern)
    return False


def is_protected_path(
    relative_path: str | Path,
    patterns: Sequence[str] = DEFAULT_PROTECTED_PATTERNS,
) -> bool:
    """Return whether a repository-relative path matches a protected pattern."""

    normalized = Path(relative_path).as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return any(_matches_protected_pattern(normalized, pattern) for pattern in patterns)


def validate_repo_scope(
    repo_root: str | Path,
    candidate: str | Path,
    *,
    protected_patterns: Sequence[str] = DEFAULT_PROTECTED_PATTERNS,
    allow_protected: bool = False,
) -> Path:
    """Resolve a candidate path and require it to stay inside the repository."""

    root = Path(repo_root).expanduser().resolve()
    if not root.is_dir():
        raise SafetyViolation(f"Repository root is not a directory: {root}")

    raw_candidate = Path(candidate).expanduser()
    scoped_candidate = raw_candidate if raw_candidate.is_absolute() else root / raw_candidate
    resolved_candidate = scoped_candidate.resolve(strict=False)

    try:
        common_path = Path(os.path.commonpath((str(root), str(resolved_candidate))))
    except ValueError as exc:
        raise SafetyViolation(f"Path is outside repository scope: {candidate}") from exc
    if common_path != root:
        raise SafetyViolation(f"Path is outside repository scope: {candidate}")

    relative_path = resolved_candidate.relative_to(root).as_posix()
    if relative_path != "." and not allow_protected:
        if is_protected_path(relative_path, protected_patterns):
            raise SafetyViolation(f"Path is protected: {relative_path}")
    return resolved_candidate


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _parse_porcelain(output: str) -> tuple[DirtyEntry, ...]:
    chunks = output.split("\0")
    entries: list[DirtyEntry] = []
    index = 0
    while index < len(chunks):
        raw_entry = chunks[index]
        index += 1
        if not raw_entry:
            continue
        if len(raw_entry) < 3:
            entries.append(DirtyEntry(status="??", path=raw_entry))
            continue

        status = raw_entry[:2]
        path = raw_entry[3:] if raw_entry[2] == " " else raw_entry[2:].lstrip()
        original_path = None
        if (status[0] in "RC" or status[1] in "RC") and index < len(chunks):
            original_path = chunks[index] or None
            index += 1
        entries.append(DirtyEntry(status=status, path=path, original_path=original_path))
    return tuple(entries)


def report_dirty_state(
    repo_root: str | Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    which: Callable[[str], str | None] | None = None,
    timeout_seconds: float = 5.0,
) -> DirtyState:
    """Report Git dirty state without modifying repository state."""

    root = Path(repo_root).expanduser().resolve()
    command_runner = runner or subprocess.run
    command_lookup = which or shutil.which
    if command_lookup("git") is None:
        return DirtyState(
            available=False,
            is_repository=False,
            dirty=False,
            error="git executable is unavailable",
        )

    try:
        completed = command_runner(
            ["git", "-C", str(root), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return DirtyState(
            available=True,
            is_repository=False,
            dirty=False,
            error="git status timed out",
        )
    except OSError as exc:
        return DirtyState(
            available=False,
            is_repository=False,
            dirty=False,
            error=redact_secrets(str(exc)),
        )

    if completed.returncode != 0:
        error = _text(completed.stderr).strip() or _text(completed.stdout).strip()
        return DirtyState(
            available=True,
            is_repository=False,
            dirty=False,
            error=redact_secrets(error or "not a Git repository"),
        )

    entries = _parse_porcelain(_text(completed.stdout))
    return DirtyState(
        available=True,
        is_repository=True,
        dirty=bool(entries),
        entries=entries,
    )


def validate_automated_command(argv: Sequence[str]) -> tuple[str, ...]:
    """Reject commit, push, publish, release, and deployment commands."""

    if not argv or any(not isinstance(part, str) or not part for part in argv):
        raise SafetyViolation("Commands must be a non-empty argv sequence")

    normalized = tuple(argv)
    program = Path(normalized[0]).name.lower()
    arguments = tuple(part.lower() for part in normalized[1:])

    if program in {"sh", "bash", "zsh", "fish", "cmd", "cmd.exe", "powershell", "pwsh"}:
        if any(flag in arguments for flag in ("-c", "/c", "-command")):
            raise SafetyViolation("Shell command strings are disabled")
    if program == "git" and any(action in arguments for action in ("commit", "push", "tag")):
        raise SafetyViolation("Automated Git commit, push, and tag actions are disabled")
    if program in {"npm", "pnpm", "yarn", "pip", "twine"}:
        if any(action in arguments for action in ("publish", "deploy", "release")):
            raise SafetyViolation("Automated publish and deploy actions are disabled")
    if program == "kubectl" and any(
        action in arguments
        for action in ("apply", "create", "delete", "patch", "replace", "rollout", "scale")
    ):
        raise SafetyViolation("Automated deployment actions are disabled")
    if program == "helm" and any(
        action in arguments for action in ("install", "upgrade", "uninstall", "rollback")
    ):
        raise SafetyViolation("Automated deployment actions are disabled")
    if program == "terraform" and any(action in arguments for action in ("apply", "destroy")):
        raise SafetyViolation("Automated infrastructure changes are disabled")
    if program == "docker" and any(action in arguments for action in ("push", "deploy")):
        raise SafetyViolation("Automated publish and deploy actions are disabled")
    if program.startswith("python"):
        scripts = (
            Path(argument).stem.lower()
            for argument in arguments
            if argument.lower().endswith(".py")
        )
        if any(script in {"deploy", "deployment", "publish", "release"} for script in scripts):
            raise SafetyViolation("Automated deployment actions are disabled")
    if any(action in arguments for action in ("deploy", "deployment")):
        raise SafetyViolation("Automated deployment actions are disabled")
    return normalized


def _redact_text(text: str, extra_values: Sequence[str]) -> str:
    redacted = _PRIVATE_KEY_BLOCK.sub(REDACTED, text)
    redacted = _AUTH_TOKEN.sub(rf"\1{REDACTED}", redacted)
    redacted = _JSON_SECRET.sub(
        lambda match: f"{match.group('prefix')}{REDACTED}{match.group('suffix')}",
        redacted,
    )
    redacted = _ASSIGNED_SECRET.sub(
        lambda match: f"{match.group('prefix')}{match.group('quote')}{REDACTED}{match.group('quote')}",
        redacted,
    )
    redacted = _KNOWN_TOKEN.sub(REDACTED, redacted)
    redacted = _URL_CREDENTIALS.sub(rf"\g<scheme>{REDACTED}@", redacted)
    for value in sorted({value for value in extra_values if value}, key=len, reverse=True):
        redacted = redacted.replace(value, REDACTED)
    return redacted


def redact_secrets(value: Any, *, extra_values: Sequence[str] = ()) -> Any:
    """Redact common credentials from strings and nested JSON-like values."""

    if isinstance(value, str):
        return _redact_text(value, extra_values)
    if isinstance(value, Mapping):
        redacted_mapping: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _SECRET_KEY_NAME.fullmatch(key):
                redacted_mapping[key] = REDACTED
            else:
                redacted_mapping[key] = redact_secrets(item, extra_values=extra_values)
        return redacted_mapping
    if isinstance(value, tuple):
        return tuple(redact_secrets(item, extra_values=extra_values) for item in value)
    if isinstance(value, list):
        return [redact_secrets(item, extra_values=extra_values) for item in value]
    return value


class SafetyGuard:
    """Repository-scoped facade used by deterministic CLI commands."""

    def __init__(
        self,
        repo_root: str | Path,
        *,
        protected_patterns: Sequence[str] = DEFAULT_PROTECTED_PATTERNS,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        which: Callable[[str], str | None] | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        if not self.repo_root.is_dir():
            raise SafetyViolation(f"Repository root is not a directory: {self.repo_root}")
        self.protected_patterns = tuple(protected_patterns)
        self._runner = runner
        self._which = which

    def validate_path(self, candidate: str | Path, *, allow_protected: bool = False) -> Path:
        return validate_repo_scope(
            self.repo_root,
            candidate,
            protected_patterns=self.protected_patterns,
            allow_protected=allow_protected,
        )

    def dirty_state(self, *, timeout_seconds: float = 5.0) -> DirtyState:
        return report_dirty_state(
            self.repo_root,
            runner=self._runner,
            which=self._which,
            timeout_seconds=timeout_seconds,
        )

    def validate_command(self, argv: Sequence[str]) -> tuple[str, ...]:
        return validate_automated_command(argv)
