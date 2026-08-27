"""Validated task decomposition without inventing fake parallelism."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .contracts import Contract


class DecompositionError(ValueError):
    """Raised for invalid dependency graphs or unsafe parallel scopes."""


@dataclass(frozen=True, slots=True)
class TaskUnit:
    unit_id: str
    goal: str
    acceptance_criteria: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    active_files: tuple[str, ...] = ()
    complexity: int = 5
    parallelizable: bool = False

    def to_contract(self, parent: Contract) -> Contract:
        return Contract(
            goal=self.goal,
            constraints=list(parent.constraints),
            acceptance_criteria=list(self.acceptance_criteria),
            non_goals=list(parent.non_goals),
            risk=parent.risk,
            ambiguity=parent.ambiguity,
            failure_conditions=list(parent.failure_conditions),
            task_type=parent.task_type,
            expected_scope="small" if len(self.active_files) <= 2 else "medium",
            active_files=list(self.active_files),
            metadata={"parent_goal": parent.goal, "unit_id": self.unit_id},
        )


@dataclass(frozen=True, slots=True)
class TaskGraph:
    units: tuple[TaskUnit, ...]

    def __post_init__(self) -> None:
        identifiers = [unit.unit_id for unit in self.units]
        if len(identifiers) != len(set(identifiers)):
            raise DecompositionError("task unit identifiers must be unique")
        known = set(identifiers)
        for unit in self.units:
            missing = set(unit.dependencies) - known
            if missing:
                raise DecompositionError(f"unknown dependencies for {unit.unit_id}: {sorted(missing)}")
        self.topological_order()
        self._validate_parallel_scopes()

    def topological_order(self) -> tuple[TaskUnit, ...]:
        remaining = {unit.unit_id: unit for unit in self.units}
        completed: set[str] = set()
        ordered: list[TaskUnit] = []
        while remaining:
            ready = [unit for unit in remaining.values() if set(unit.dependencies) <= completed]
            if not ready:
                raise DecompositionError("task dependency graph contains a cycle")
            for unit in sorted(ready, key=lambda item: item.unit_id):
                ordered.append(unit)
                completed.add(unit.unit_id)
                remaining.pop(unit.unit_id)
        return tuple(ordered)

    def ready(self, completed: Iterable[str]) -> tuple[TaskUnit, ...]:
        done = set(completed)
        return tuple(
            unit for unit in self.units
            if unit.unit_id not in done and set(unit.dependencies) <= done
        )

    def _validate_parallel_scopes(self) -> None:
        parallel = [unit for unit in self.units if unit.parallelizable]
        for index, left in enumerate(parallel):
            for right in parallel[index + 1:]:
                if set(left.active_files) & set(right.active_files):
                    raise DecompositionError(
                        f"parallel units {left.unit_id} and {right.unit_id} overlap write scope"
                    )


class TaskDecomposer:
    """Use explicit Layer-1 decomposition or retain a single safe unit."""

    def decompose(
        self,
        contract: Contract,
        subtasks: Iterable[Mapping[str, Any]] | None = None,
    ) -> TaskGraph:
        definitions = list(subtasks or ())
        if not definitions:
            return TaskGraph((TaskUnit(
                unit_id="task-1",
                goal=contract.goal,
                acceptance_criteria=tuple(contract.acceptance_criteria),
                active_files=tuple(contract.active_files),
                complexity=5,
            ),))
        units = []
        for index, definition in enumerate(definitions, 1):
            unit_id = str(definition.get("id", f"task-{index}")).strip()
            goal = str(definition.get("goal", "")).strip()
            criteria = tuple(str(item).strip() for item in definition.get("acceptance_criteria", ()))
            if not unit_id or not goal or not criteria:
                raise DecompositionError("each subtask requires id, goal, and acceptance criteria")
            complexity = int(definition.get("complexity", 5))
            if not 0 <= complexity <= 10:
                raise DecompositionError("subtask complexity must be between 0 and 10")
            units.append(TaskUnit(
                unit_id=unit_id,
                goal=goal,
                acceptance_criteria=criteria,
                dependencies=tuple(str(item) for item in definition.get("dependencies", ())),
                active_files=tuple(str(item) for item in definition.get("active_files", ())),
                complexity=complexity,
                parallelizable=bool(definition.get("parallelizable", False)),
            ))
        return TaskGraph(tuple(units))
