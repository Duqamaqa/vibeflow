"""Isolated task workspace creation, cleanup, and reviewed promotion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Callable, Sequence

from .changes import ApplyResult, StructuredChangeApplier
from .safety import DEFAULT_PROTECTED_PATTERNS, SafetyGuard, is_protected_path, redact_secrets


class WorkspaceError(RuntimeError):
    """Raised when an isolated workspace cannot be created or promoted."""


@dataclass(frozen=True, slots=True)
class WorkspaceInfo:
    path: Path
    strategy: str
    protected_dirty_paths: tuple[str, ...]


class IsolatedWorkspace:
    """Run changes away from the target, then promote only reviewed file content."""

    def __init__(
        self,
        repo_root: str | Path,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        if not self.repo_root.is_dir():
            raise WorkspaceError(f"Repository root is not a directory: {self.repo_root}")
        self._runner = runner or subprocess.run
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self.info: WorkspaceInfo | None = None
        self.applier: StructuredChangeApplier | None = None

    def __enter__(self) -> "IsolatedWorkspace":
        self._temporary = tempfile.TemporaryDirectory(prefix="vibeflow-")
        workspace_path = Path(self._temporary.name) / "workspace"
        dirty_paths = self._dirty_paths()
        if self._create_git_worktree(workspace_path):
            strategy = "git-worktree"
            protected = dirty_paths
        else:
            self._copy_workspace(workspace_path)
            strategy = "safe-copy"
            protected = ()
        patterns = (*DEFAULT_PROTECTED_PATTERNS, *protected)
        self.info = WorkspaceInfo(workspace_path.resolve(), strategy, protected)
        self.applier = StructuredChangeApplier(
            self.info.path,
            protected_patterns=patterns,
        )
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.info is not None and self.info.strategy == "git-worktree":
            self._git("worktree", "remove", "--force", str(self.info.path), check=False)
        if self._temporary is not None:
            self._temporary.cleanup()

    @property
    def path(self) -> Path:
        if self.info is None:
            raise WorkspaceError("Workspace has not been entered")
        return self.info.path

    def promote(self) -> ApplyResult:
        if self.applier is None:
            raise WorkspaceError("Workspace has not been entered")
        proposal = self.applier.final_proposal()
        target_applier = StructuredChangeApplier(self.repo_root)
        return target_applier.apply(proposal)

    def _dirty_paths(self) -> tuple[str, ...]:
        state = SafetyGuard(self.repo_root).dirty_state()
        if not state.available or not state.is_repository:
            return ()
        paths: list[str] = []
        for entry in state.entries:
            paths.append(entry.path)
            if entry.original_path:
                paths.append(entry.original_path)
        return tuple(dict.fromkeys(path for path in paths if path))

    def _create_git_worktree(self, destination: Path) -> bool:
        top_level = self._git("rev-parse", "--show-toplevel", check=False)
        head = self._git("rev-parse", "--verify", "HEAD", check=False)
        if top_level.returncode != 0 or head.returncode != 0:
            return False
        try:
            reported = Path(top_level.stdout.strip()).resolve(strict=True)
        except OSError:
            return False
        if reported != self.repo_root:
            return False
        result = self._git(
            "worktree",
            "add",
            "--detach",
            str(destination),
            "HEAD",
            check=False,
        )
        return result.returncode == 0 and destination.is_dir()

    def _copy_workspace(self, destination: Path) -> None:
        def ignored(directory: str, names: Sequence[str]) -> set[str]:
            base = Path(directory)
            excluded: set[str] = set()
            for name in names:
                candidate = base / name
                try:
                    relative = candidate.relative_to(self.repo_root).as_posix()
                except ValueError:
                    continue
                if (
                    name in {".git", ".vibeflow", "__pycache__", ".pytest_cache"}
                    or is_protected_path(relative)
                ):
                    excluded.add(name)
            return excluded
        try:
            shutil.copytree(self.repo_root, destination, symlinks=True, ignore=ignored)
        except OSError as exc:
            raise WorkspaceError(f"Cannot create isolated safe copy: {redact_secrets(str(exc))}") from exc

    def _git(self, *args: str, check: bool) -> subprocess.CompletedProcess[str]:
        try:
            return self._runner(
                ["git", "-C", str(self.repo_root), *args],
                capture_output=True,
                text=True,
                timeout=30,
                check=check,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return subprocess.CompletedProcess(
                ["git", *args],
                1,
                "",
                redact_secrets(str(exc)),
            )
