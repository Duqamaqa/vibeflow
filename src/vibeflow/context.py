"""Repository-native, budgeted context retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil
import subprocess
from typing import Iterable

from .contracts import Contract
from .safety import is_protected_path


class ContextError(RuntimeError):
    """Raised when requested context is unsafe or unavailable."""


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


@dataclass(frozen=True, slots=True)
class ContextItem:
    name: str
    content: str
    priority: int
    kind: str

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.content)


@dataclass(slots=True)
class ContextBundle:
    items: list[ContextItem] = field(default_factory=list)
    max_tokens: int = 8_000

    @property
    def estimated_tokens(self) -> int:
        return sum(item.tokens for item in self.items)

    def trim(self) -> "ContextBundle":
        kept = list(self.items)
        while sum(item.tokens for item in kept) > self.max_tokens:
            removable = [item for item in kept if item.kind != "contract"]
            if not removable:
                break
            victim = min(removable, key=lambda item: (item.priority, -item.tokens))
            kept.remove(victim)
        return ContextBundle(items=kept, max_tokens=self.max_tokens)

    def render(self) -> str:
        return "\n\n".join(
            f"=== {item.kind.upper()}: {item.name} ===\n{item.content}" for item in self.items
        )


class ContextManager:
    """Retrieve targeted files and compact project memory on demand."""

    MEMORY_FILES = ("architecture.md", "coding_rules.md", "decisions.md", "current_task.md")

    def __init__(self, repo_root: Path | str | None = None) -> None:
        self.repo_root = Path(repo_root or self.get_repo_root()).resolve()

    @staticmethod
    def get_repo_root() -> Path:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return Path(result.stdout.strip()) if result.returncode == 0 else Path.cwd()

    def resolve_path(self, file_path: Path | str) -> Path:
        candidate = Path(file_path)
        if not candidate.is_absolute():
            candidate = self.repo_root / candidate
        candidate = candidate.resolve()
        if not candidate.is_relative_to(self.repo_root):
            raise ContextError(f"path is outside repository: {file_path}")
        return candidate

    def read_file(self, file_path: Path | str, *, max_bytes: int = 128_000) -> str | None:
        path = self.resolve_path(file_path)
        relative = path.relative_to(self.repo_root)
        if relative != Path(".") and is_protected_path(relative):
            return None
        try:
            if not path.is_file() or path.stat().st_size > max_bytes:
                return None
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def search_files(
        self,
        pattern: str,
        paths: Iterable[Path | str] | None = None,
        *,
        limit: int = 50,
    ) -> list[Path]:
        safe_paths = [self.resolve_path(path) for path in (paths or (self.repo_root,))]
        relative = [str(path.relative_to(self.repo_root)) or "." for path in safe_paths]
        if shutil.which("rg"):
            command = ["rg", "--files-with-matches", "--fixed-strings", "--", pattern, *relative]
        else:
            command = ["git", "grep", "-l", "--fixed-strings", "--", pattern, *relative]
        result = subprocess.run(
            command,
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode not in {0, 1}:
            return []
        return [self.resolve_path(line) for line in result.stdout.splitlines()[:limit] if line.strip()]

    def repository_manifest(self, *, limit: int = 300) -> tuple[str, ...]:
        """Return a bounded, secret-filtered repository file manifest."""

        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            candidates = result.stdout.splitlines()
        else:
            candidates = [
                path.relative_to(self.repo_root).as_posix()
                for path in self.repo_root.rglob("*")
                if path.is_file() and not path.is_symlink()
            ]
        return tuple(
            path for path in candidates
            if path and not is_protected_path(path)
        )[:limit]

    def git_history(self, file_path: Path | str, *, limit: int = 5) -> str:
        path = self.resolve_path(file_path)
        relative = str(path.relative_to(self.repo_root))
        result = subprocess.run(
            ["git", "log", f"-{limit}", "--oneline", "--", relative],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def get_ai_metadata(self) -> dict[str, str]:
        metadata: dict[str, str] = {}
        for name in self.MEMORY_FILES:
            content = self.read_file(Path(".ai") / name)
            if content:
                metadata[Path(name).stem] = content
        return metadata

    def build_context(
        self,
        contract: Contract,
        active_files: Iterable[str] = (),
        *,
        max_tokens: int = 8_000,
    ) -> ContextBundle:
        items = [
            ContextItem("task", json.dumps(contract.to_dict(), indent=2), 100, "contract")
        ]
        priorities = {"architecture": 95, "coding_rules": 90, "decisions": 85, "current_task": 80}
        for name, content in self.get_ai_metadata().items():
            items.append(ContextItem(name, content, priorities.get(name, 70), "memory"))
        seen: set[str] = set()
        requested_files = (*contract.active_files, *active_files)
        if not requested_files:
            manifest = self.repository_manifest()
            if manifest:
                items.append(
                    ContextItem(
                        "repository-manifest",
                        "\n".join(manifest),
                        65,
                        "manifest",
                    )
                )
            discovered: list[str] = []
            terms = [
                term.strip(".,:;!?()[]{}\"'")
                for term in contract.goal.split()
                if len(term.strip(".,:;!?()[]{}\"'")) >= 4
            ]
            for term in tuple(dict.fromkeys(terms))[:6]:
                for path in self.search_files(term, limit=8):
                    relative = path.relative_to(self.repo_root).as_posix()
                    if relative not in discovered and not is_protected_path(relative):
                        discovered.append(relative)
                    if len(discovered) >= 8:
                        break
                if len(discovered) >= 8:
                    break
            requested_files = tuple(discovered)
        for file_path in requested_files:
            if file_path in seen:
                continue
            seen.add(file_path)
            content = self.read_file(file_path)
            if content is not None:
                items.append(ContextItem(file_path, content, 75, "file"))
        return ContextBundle(items=items, max_tokens=max_tokens).trim()
