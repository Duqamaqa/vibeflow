"""Validated structured file changes with rollback and diff generation."""

from __future__ import annotations

from dataclasses import dataclass
import difflib
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Mapping, Sequence

from .safety import SafetyGuard, SafetyViolation


class ChangeError(SafetyViolation):
    """Raised when a proposed file operation is invalid or unsafe."""


def file_sha256(path: Path) -> str:
    """Return the SHA-256 digest of one regular file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class FileOperation:
    """One explicit create, update, delete, or rename operation."""

    op: str
    path: str
    content: str | None = None
    destination: str | None = None
    expected_sha256: str | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FileOperation":
        if not isinstance(payload, Mapping):
            raise ChangeError("Each operation must be an object")
        allowed = {"op", "path", "content", "destination", "expected_sha256"}
        unknown = set(payload) - allowed
        if unknown:
            raise ChangeError(f"Unknown operation keys: {', '.join(sorted(unknown))}")
        return cls(
            op=str(payload.get("op", "")).strip().lower(),
            path=str(payload.get("path", "")).strip(),
            content=payload.get("content") if isinstance(payload.get("content"), str) else None,
            destination=(
                str(payload["destination"]).strip()
                if isinstance(payload.get("destination"), str)
                else None
            ),
            expected_sha256=(
                str(payload["expected_sha256"]).strip().lower()
                if isinstance(payload.get("expected_sha256"), str)
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "path": self.path,
            "content": self.content,
            "destination": self.destination,
            "expected_sha256": self.expected_sha256,
        }


@dataclass(frozen=True, slots=True)
class ChangeProposal:
    """A complete worker proposal that can be deterministically validated."""

    operations: tuple[FileOperation, ...]

    @classmethod
    def from_payload(cls, payload: Any) -> "ChangeProposal":
        if not isinstance(payload, list):
            raise ChangeError("operations must be a JSON array")
        operations = tuple(FileOperation.from_dict(item) for item in payload)
        if not operations:
            raise ChangeError("A successful change proposal must contain operations")
        if len(operations) > 100:
            raise ChangeError("A change proposal cannot exceed 100 operations")
        return cls(operations)

    @property
    def changed_files(self) -> tuple[str, ...]:
        paths: list[str] = []
        for operation in self.operations:
            paths.append(operation.path)
            if operation.destination:
                paths.append(operation.destination)
        return tuple(dict.fromkeys(paths))

    def to_dict(self) -> dict[str, Any]:
        return {"operations": [operation.to_dict() for operation in self.operations]}


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    """The original state of one path before a task touched it."""

    existed: bool
    content: bytes = b""
    mode: int | None = None


@dataclass(frozen=True, slots=True)
class ApplyResult:
    applied: bool
    changed_files: tuple[str, ...]
    diff: str


class StructuredChangeApplier:
    """Apply full-file operations after path, hash, and overlap validation."""

    def __init__(
        self,
        repo_root: str | Path,
        *,
        protected_patterns: Sequence[str] | None = None,
        max_file_bytes: int = 2_000_000,
    ) -> None:
        if max_file_bytes <= 0:
            raise ValueError("max_file_bytes must be positive")
        guard_options = {}
        if protected_patterns is not None:
            guard_options["protected_patterns"] = protected_patterns
        self.guard = SafetyGuard(repo_root, **guard_options)
        self.repo_root = self.guard.repo_root
        self.max_file_bytes = max_file_bytes
        self._baseline: dict[str, FileSnapshot] = {}
        self._touched: list[str] = []

    @property
    def touched_files(self) -> tuple[str, ...]:
        return tuple(self._touched)

    def apply(self, proposal: ChangeProposal) -> ApplyResult:
        resolved = self._validate(proposal)
        newly_touched = [path for path in resolved if path not in self._baseline]
        transaction_baseline = {
            relative_path: self._snapshot(self.repo_root / relative_path)
            for relative_path in resolved
        }
        for relative_path in newly_touched:
            self._baseline[relative_path] = transaction_baseline[relative_path]
        try:
            for operation in proposal.operations:
                self._apply_operation(operation)
        except Exception:
            self._restore_snapshots(transaction_baseline)
            for relative_path in newly_touched:
                self._baseline.pop(relative_path, None)
            raise
        for relative_path in resolved:
            if relative_path not in self._touched:
                self._touched.append(relative_path)
        return ApplyResult(True, proposal.changed_files, self.diff())

    def rollback(self) -> None:
        self._restore(tuple(self._baseline))
        self._baseline.clear()
        self._touched.clear()

    def diff(self) -> str:
        chunks: list[str] = []
        for relative_path in sorted(self._baseline):
            before = self._baseline[relative_path]
            current = self._snapshot(self.repo_root / relative_path)
            if before.existed == current.existed and before.content == current.content:
                continue
            before_text = self._decode_for_diff(before.content, relative_path) if before.existed else ""
            after_text = self._decode_for_diff(current.content, relative_path) if current.existed else ""
            chunks.extend(
                difflib.unified_diff(
                    before_text.splitlines(keepends=True),
                    after_text.splitlines(keepends=True),
                    fromfile=f"a/{relative_path}" if before.existed else "/dev/null",
                    tofile=f"b/{relative_path}" if current.existed else "/dev/null",
                )
            )
        return "".join(chunks)

    def final_proposal(self) -> ChangeProposal:
        operations: list[FileOperation] = []
        for relative_path in sorted(self._baseline):
            before = self._baseline[relative_path]
            current = self._snapshot(self.repo_root / relative_path)
            if before.existed == current.existed and before.content == current.content:
                continue
            expected = hashlib.sha256(before.content).hexdigest() if before.existed else None
            if not current.existed:
                operations.append(FileOperation("delete", relative_path, expected_sha256=expected))
            elif not before.existed:
                operations.append(
                    FileOperation("create", relative_path, content=current.content.decode("utf-8"))
                )
            else:
                operations.append(
                    FileOperation(
                        "update",
                        relative_path,
                        content=current.content.decode("utf-8"),
                        expected_sha256=expected,
                    )
                )
        if not operations:
            raise ChangeError("Task produced no file changes")
        return ChangeProposal(tuple(operations))

    def _validate(self, proposal: ChangeProposal) -> tuple[str, ...]:
        paths: list[str] = []
        for operation in proposal.operations:
            if operation.op not in {"create", "update", "delete", "rename"}:
                raise ChangeError(f"Unsupported operation: {operation.op or '(missing)'}")
            path = self._relative(operation.path)
            paths.append(path)
            target = self.repo_root / path
            if operation.op == "create":
                self._require_content(operation)
                if target.exists() or target.is_symlink():
                    raise ChangeError(f"create target already exists: {path}")
                if operation.expected_sha256 is not None or operation.destination is not None:
                    raise ChangeError("create accepts only path and content")
            elif operation.op == "update":
                self._require_regular_file(target, path)
                self._require_content(operation)
                self._require_hash(operation, target, path)
                if operation.destination is not None:
                    raise ChangeError("update does not accept destination")
            elif operation.op == "delete":
                self._require_regular_file(target, path)
                self._require_hash(operation, target, path)
                if operation.content is not None or operation.destination is not None:
                    raise ChangeError("delete accepts only path and expected_sha256")
            else:
                self._require_regular_file(target, path)
                self._require_hash(operation, target, path)
                if operation.content is not None or not operation.destination:
                    raise ChangeError("rename requires destination and no content")
                destination = self._relative(operation.destination)
                destination_path = self.repo_root / destination
                if destination_path.exists() or destination_path.is_symlink():
                    raise ChangeError(f"rename destination already exists: {destination}")
                paths.append(destination)
        if len(paths) != len(set(paths)):
            raise ChangeError("A proposal cannot touch the same path more than once")
        return tuple(paths)

    def _relative(self, candidate: str) -> str:
        if not candidate or Path(candidate).is_absolute():
            raise ChangeError("Operation paths must be non-empty and repository-relative")
        try:
            path = self.guard.validate_path(candidate)
        except SafetyViolation as exc:
            raise ChangeError(str(exc)) from exc
        relative = path.relative_to(self.repo_root).as_posix()
        if relative == ".":
            raise ChangeError("Repository root cannot be a file-operation target")
        return relative

    def _require_content(self, operation: FileOperation) -> None:
        if operation.content is None:
            raise ChangeError(f"{operation.op} requires string content")
        if "\x00" in operation.content:
            raise ChangeError("Binary or NUL-containing content is not supported")
        if len(operation.content.encode("utf-8")) > self.max_file_bytes:
            raise ChangeError(f"File content exceeds {self.max_file_bytes} bytes")

    @staticmethod
    def _require_regular_file(path: Path, relative_path: str) -> None:
        if path.is_symlink() or not path.is_file():
            raise ChangeError(f"Expected a regular existing file: {relative_path}")

    @staticmethod
    def _require_hash(operation: FileOperation, path: Path, relative_path: str) -> None:
        expected = operation.expected_sha256
        if not expected or len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
            raise ChangeError(f"{operation.op} requires a lowercase SHA-256 precondition")
        actual = file_sha256(path)
        if actual != expected:
            raise ChangeError(f"File changed since proposal context: {relative_path}")

    def _apply_operation(self, operation: FileOperation) -> None:
        path = self.guard.validate_path(operation.path)
        if operation.op in {"create", "update"}:
            self._atomic_write(path, operation.content or "")
        elif operation.op == "delete":
            path.unlink()
            self._remove_empty_parents(path.parent)
        else:
            destination = self.guard.validate_path(operation.destination or "")
            destination.parent.mkdir(parents=True, exist_ok=True)
            path.rename(destination)
            self._remove_empty_parents(path.parent)

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing_mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
        descriptor, temporary_name = tempfile.mkstemp(prefix=".vibeflow-", dir=path.parent)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            temporary_path.chmod(existing_mode)
            os.replace(temporary_path, path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    @staticmethod
    def _snapshot(path: Path) -> FileSnapshot:
        if path.is_symlink():
            raise ChangeError(f"Symlink targets are not supported: {path}")
        if not path.exists():
            return FileSnapshot(False)
        if not path.is_file():
            raise ChangeError(f"Only regular files can be changed: {path}")
        return FileSnapshot(True, path.read_bytes(), path.stat().st_mode & 0o777)

    def _restore(self, relative_paths: Iterable[str]) -> None:
        snapshots = {
            relative_path: self._baseline[relative_path]
            for relative_path in relative_paths
            if relative_path in self._baseline
        }
        self._restore_snapshots(snapshots)

    def _restore_snapshots(self, snapshots: Mapping[str, FileSnapshot]) -> None:
        for relative_path, snapshot in reversed(tuple(snapshots.items())):
            path = self.guard.validate_path(relative_path)
            if snapshot.existed:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(snapshot.content)
                if snapshot.mode is not None:
                    path.chmod(snapshot.mode)
            elif path.exists() and path.is_file():
                path.unlink()
                self._remove_empty_parents(path.parent)

    def _remove_empty_parents(self, directory: Path) -> None:
        while directory != self.repo_root:
            try:
                directory.rmdir()
            except OSError:
                break
            directory = directory.parent

    @staticmethod
    def _decode_for_diff(content: bytes, relative_path: str) -> str:
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ChangeError(f"Binary file changes are not supported: {relative_path}") from exc
