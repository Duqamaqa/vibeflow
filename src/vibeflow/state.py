"""Small atomic store for current task state."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import tempfile
from typing import Any


@dataclass(frozen=True, slots=True)
class TaskState:
    task_id: str
    status: str
    goal: str
    tier: str
    strategy: str
    updated_at: float
    blocker: str | None = None


class TaskStateStore:
    """Persist non-secret state without making it model context by default."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def load(self) -> TaskState | None:
        if not self.path.is_file():
            return None
        try:
            data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
            return TaskState(**data)
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    def save(self, state: TaskState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            delete=False,
        ) as handle:
            json.dump(asdict(state), handle, indent=2, sort_keys=True)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(self.path)
