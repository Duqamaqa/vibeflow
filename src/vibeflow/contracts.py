"""Task contracts and approval policy for Vibeflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable, Mapping


class ContractError(ValueError):
    """Raised when a task contract is invalid."""


class Risk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Ambiguity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalState(StrEnum):
    AUTO_APPROVED = "auto-approved"
    NEEDS_APPROVAL = "needs-approval"
    INVALID = "invalid"


def _strings(values: Iterable[str] | None) -> list[str]:
    return [str(value).strip() for value in values or () if str(value).strip()]


@dataclass(slots=True)
class Contract:
    """An explicit, serializable agreement for one coding task."""

    goal: str
    constraints: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    risk: Risk | str = Risk.LOW
    ambiguity: Ambiguity | str = Ambiguity.LOW
    failure_conditions: list[str] = field(default_factory=list)
    task_type: str = "implementation"
    expected_scope: str = "small"
    active_files: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.goal = self.goal.strip()
        self.constraints = _strings(self.constraints)
        self.acceptance_criteria = _strings(self.acceptance_criteria)
        self.non_goals = _strings(self.non_goals)
        self.failure_conditions = _strings(self.failure_conditions)
        self.active_files = _strings(self.active_files)
        try:
            self.risk = Risk(self.risk)
            self.ambiguity = Ambiguity(self.ambiguity)
        except ValueError as exc:
            raise ContractError(str(exc)) from exc
        if self.expected_scope not in {"small", "medium", "large"}:
            raise ContractError("expected_scope must be small, medium, or large")

    def validation_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.goal:
            errors.append("goal is required")
        if not self.acceptance_criteria:
            errors.append("at least one acceptance criterion is required")
        return errors

    def approval_state(self) -> ApprovalState:
        if self.validation_errors():
            return ApprovalState.INVALID
        if self.risk is Risk.HIGH or self.ambiguity is not Ambiguity.LOW:
            return ApprovalState.NEEDS_APPROVAL
        return ApprovalState.AUTO_APPROVED

    def is_clear_and_low_risk(self) -> bool:
        return self.approval_state() is ApprovalState.AUTO_APPROVED

    def requires_user_approval(self) -> bool:
        return self.approval_state() is not ApprovalState.AUTO_APPROVED

    def reverse_questions(self) -> list[str]:
        """Return only questions that can materially change implementation."""
        questions: list[str] = []
        if not self.goal:
            questions.append("What concrete outcome should Vibeflow produce?")
        if not self.acceptance_criteria:
            questions.append("What observable result proves the task is complete?")
        if self.ambiguity is Ambiguity.MEDIUM:
            questions.append("Which interpretation should be treated as authoritative?")
        elif self.ambiguity is Ambiguity.HIGH:
            questions.append("Please resolve the conflicting or missing requirements before execution.")
        if self.risk is Risk.HIGH:
            questions.append("Do you approve the high-risk scope and stated failure conditions?")
        return questions

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "constraints": list(self.constraints),
            "acceptance_criteria": list(self.acceptance_criteria),
            "non_goals": list(self.non_goals),
            "risk": self.risk.value,
            "ambiguity": self.ambiguity.value,
            "failure_conditions": list(self.failure_conditions),
            "task_type": self.task_type,
            "expected_scope": self.expected_scope,
            "active_files": list(self.active_files),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Contract":
        return cls(
            goal=str(data.get("goal", "")),
            constraints=data.get("constraints", ()),
            acceptance_criteria=data.get("acceptance_criteria", ()),
            non_goals=data.get("non_goals", ()),
            risk=data.get("risk", Risk.LOW),
            ambiguity=data.get("ambiguity", Ambiguity.LOW),
            failure_conditions=data.get("failure_conditions", ()),
            task_type=str(data.get("task_type", "implementation")),
            expected_scope=str(data.get("expected_scope", "small")),
            active_files=data.get("active_files", ()),
            metadata=dict(data.get("metadata", {})),
        )


def contract_from_request(
    goal: str,
    *,
    description: str | None = None,
    acceptance_criteria: Iterable[str] | None = None,
    constraints: Iterable[str] | None = None,
    non_goals: Iterable[str] | None = None,
    failure_conditions: Iterable[str] | None = None,
    risk: Risk | str = Risk.LOW,
    ambiguity: Ambiguity | str = Ambiguity.LOW,
    task_type: str = "implementation",
    expected_scope: str = "small",
    active_files: Iterable[str] | None = None,
) -> Contract:
    full_goal = goal.strip()
    if description and description.strip():
        full_goal = f"{full_goal}\n\n{description.strip()}"
    criteria = _strings(acceptance_criteria)
    if full_goal and not criteria and ambiguity == Ambiguity.LOW and risk == Risk.LOW:
        if task_type == "research":
            criteria = [
                "The analysis uses live, attributable evidence and does not fabricate facts or contacts."
            ]
        else:
            criteria = ["The requested outcome is implemented and deterministic verification passes."]
    return Contract(
        goal=full_goal,
        constraints=_strings(constraints),
        acceptance_criteria=criteria,
        non_goals=_strings(non_goals),
        risk=risk,
        ambiguity=ambiguity,
        failure_conditions=_strings(failure_conditions),
        task_type=task_type,
        expected_scope=expected_scope,
        active_files=_strings(active_files),
    )
