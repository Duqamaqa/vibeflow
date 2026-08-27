"""Safety-focused Git worktree management using argv-only subprocesses."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Protocol, Sequence, runtime_checkable


class WorktreeError(RuntimeError):
    """Base error for worktree operations."""


class UnsafeWorktreeOperation(WorktreeError):
    """Raised when a request violates a local safety invariant."""


class DirtyWorktreeError(WorktreeError):
    """Raised before an operation that requires a clean worktree."""


class GitCommandError(WorktreeError):
    def __init__(self, argv: Sequence[str], returncode: int, stderr: str) -> None:
        self.argv = tuple(argv)
        self.returncode = returncode
        self.stderr = stderr
        message = stderr.strip() or f"git exited with status {returncode}"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class WorktreeInfo:
    path: Path
    head: str | None = None
    branch: str | None = None
    detached: bool = False
    bare: bool = False
    locked: bool = False
    prunable: bool = False


@runtime_checkable
class ArgvRunner(Protocol):
    """Injectable subprocess boundary used by unit tests and production."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        """Run an argv command without a shell."""


def _default_runner(
    argv: Sequence[str],
    *,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=str(cwd),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        timeout=30.0,
    )


class GitWorktreeManager:
    """Create and remove isolated worktrees without committing or merging."""

    _FORBIDDEN_GIT_OPERATIONS = frozenset(
        {"commit", "merge", "rebase", "push", "reset"}
    )

    def __init__(
        self,
        repo_root: str | Path,
        *,
        worktree_root: str | Path | None = None,
        runner: ArgvRunner | None = None,
    ) -> None:
        self.repo_root = self._existing_directory(repo_root, "repo_root")
        root = self.repo_root.parent if worktree_root is None else worktree_root
        self.worktree_root = self._existing_directory(root, "worktree_root")
        if runner is not None and not callable(runner):
            raise TypeError("runner must be callable")
        self._runner = runner or _default_runner
        self._repository_validated = False

    def validate_repository(self) -> None:
        result = self._git("rev-parse", "--show-toplevel")
        output = result.stdout.strip()
        if not output:
            raise WorktreeError("git did not report a repository root")
        try:
            reported_root = Path(output).resolve(strict=True)
        except OSError as error:
            raise WorktreeError("git reported an invalid repository root") from error
        if reported_root != self.repo_root:
            raise WorktreeError("repo_root is not the Git top-level directory")
        self._repository_validated = True

    def is_dirty(self, path: str | Path | None = None) -> bool:
        self._ensure_repository()
        if path is None:
            target = self.repo_root
        else:
            target = self._validate_target(path, must_exist=True, allow_repo=True)
            if target != self.repo_root:
                registered = {info.path for info in self.list_worktrees()}
                if target not in registered:
                    raise WorktreeError("path is not a registered worktree")
        return self._status_is_dirty(target)

    def assert_clean(self, path: str | Path | None = None) -> None:
        if self.is_dirty(path):
            target = self.repo_root if path is None else Path(path)
            raise DirtyWorktreeError(f"worktree has uncommitted changes: {target}")

    def create(
        self,
        path: str | Path,
        *,
        branch: str,
        start_point: str = "HEAD",
        require_clean: bool = True,
    ) -> WorktreeInfo:
        target = self._validate_target(path, must_exist=False, allow_repo=False)
        self._validate_ref(branch, "branch")
        self._validate_ref(start_point, "start_point")
        if not isinstance(require_clean, bool):
            raise TypeError("require_clean must be a bool")
        self._ensure_repository()
        if require_clean and self._status_is_dirty(self.repo_root):
            raise DirtyWorktreeError("repository has uncommitted changes")

        self._git(
            "worktree",
            "add",
            "-b",
            branch,
            str(target),
            start_point,
        )
        return WorktreeInfo(path=target, branch=branch)

    def remove(self, path: str | Path, *, force: bool = False) -> None:
        if not isinstance(force, bool):
            raise TypeError("force must be a bool")
        if force:
            raise UnsafeWorktreeOperation("forced worktree removal is not supported")
        target = self._validate_target(path, must_exist=True, allow_repo=False)
        self._ensure_repository()
        worktrees = {info.path: info for info in self.list_worktrees()}
        info = worktrees.get(target)
        if info is None:
            raise WorktreeError("path is not a registered worktree")
        if info.locked:
            raise UnsafeWorktreeOperation("locked worktrees cannot be removed")
        if self._status_is_dirty(target):
            raise DirtyWorktreeError("worktree has uncommitted changes")
        self._git("worktree", "remove", str(target))

    def list_worktrees(self) -> tuple[WorktreeInfo, ...]:
        self._ensure_repository()
        result = self._git("worktree", "list", "--porcelain")
        return self._parse_worktrees(result.stdout)

    def _ensure_repository(self) -> None:
        if not self._repository_validated:
            self.validate_repository()

    def _status_is_dirty(self, target: Path) -> bool:
        result = self._git(
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            cwd=target,
        )
        return bool(result.stdout.strip())

    def _git(
        self,
        *args: str,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if not args:
            raise ValueError("a git operation is required")
        if args[0] in self._FORBIDDEN_GIT_OPERATIONS:
            raise UnsafeWorktreeOperation(
                f"git {args[0]} is outside worktree manager scope"
            )
        argv = ("git", *args)
        result = self._runner(argv, cwd=cwd or self.repo_root)
        if not hasattr(result, "returncode"):
            raise TypeError("runner must return subprocess.CompletedProcess")
        if result.returncode != 0:
            raise GitCommandError(argv, result.returncode, result.stderr or "")
        return result

    def _validate_target(
        self,
        path: str | Path,
        *,
        must_exist: bool,
        allow_repo: bool,
    ) -> Path:
        raw_path = Path(path)
        if not raw_path.is_absolute():
            raise ValueError("worktree paths must be absolute")
        if any(ord(character) < 32 or ord(character) == 127 for character in str(raw_path)):
            raise ValueError("worktree paths must not contain control characters")
        if raw_path.is_symlink():
            raise UnsafeWorktreeOperation("symlink worktree paths are not allowed")
        try:
            target = raw_path.resolve(strict=must_exist)
        except OSError as error:
            raise ValueError(f"invalid worktree path: {raw_path}") from error

        if must_exist and not target.is_dir():
            raise ValueError("worktree path must be a directory")
        if not must_exist:
            if raw_path.exists():
                raise FileExistsError(f"worktree path already exists: {raw_path}")
            if not target.parent.is_dir():
                raise ValueError("worktree parent directory must exist")

        if target == self.repo_root:
            if allow_repo:
                return target
            raise UnsafeWorktreeOperation("the main repository is not a managed worktree")
        if target == self.worktree_root:
            raise UnsafeWorktreeOperation("worktree root itself cannot be a worktree")
        if not self._is_relative_to(target, self.worktree_root):
            raise UnsafeWorktreeOperation("worktree path is outside worktree_root")
        if self._is_relative_to(target, self.repo_root):
            raise UnsafeWorktreeOperation("nested worktrees are not allowed")
        return target

    @staticmethod
    def _existing_directory(path: str | Path, name: str) -> Path:
        try:
            resolved = Path(path).resolve(strict=True)
        except OSError as error:
            raise ValueError(f"{name} does not exist") from error
        if not resolved.is_dir():
            raise ValueError(f"{name} must be a directory")
        return resolved

    @staticmethod
    def _is_relative_to(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
        except ValueError:
            return False
        return True

    @staticmethod
    def _validate_ref(value: str, name: str) -> None:
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a string")
        invalid_fragments = ("..", "@{", "//")
        invalid_characters = set(" ~^:?*[\\")
        components = value.split("/")
        if (
            not value
            or value == "@"
            or value.startswith("-")
            or value.startswith("/")
            or value.endswith(("/", ".", ".lock"))
            or any(
                not component
                or component.startswith(".")
                or component.endswith(".lock")
                for component in components
            )
            or any(fragment in value for fragment in invalid_fragments)
            or any(character in invalid_characters for character in value)
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError(f"invalid {name}")

    @staticmethod
    def _parse_worktrees(output: str) -> tuple[WorktreeInfo, ...]:
        records: list[dict[str, str | bool]] = []
        current: dict[str, str | bool] = {}
        for line in (*output.splitlines(), ""):
            if not line:
                if current:
                    records.append(current)
                    current = {}
                continue
            key, _, value = line.partition(" ")
            current[key] = value if value else True

        worktrees = []
        for record in records:
            raw_path = record.get("worktree")
            if not isinstance(raw_path, str):
                raise WorktreeError("invalid git worktree list output")
            path = Path(raw_path).resolve(strict=False)
            branch_value = record.get("branch")
            branch = branch_value if isinstance(branch_value, str) else None
            if branch is not None and branch.startswith("refs/heads/"):
                branch = branch[len("refs/heads/") :]
            head_value = record.get("HEAD")
            head = head_value if isinstance(head_value, str) else None
            worktrees.append(
                WorktreeInfo(
                    path=path,
                    head=head,
                    branch=branch,
                    detached=bool(record.get("detached", False)),
                    bare=bool(record.get("bare", False)),
                    locked="locked" in record,
                    prunable="prunable" in record,
                )
            )
        return tuple(worktrees)


WorktreeManager = GitWorktreeManager
Worktree = WorktreeInfo
