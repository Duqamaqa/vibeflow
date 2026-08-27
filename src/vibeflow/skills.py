"""Reusable skill metadata, deterministic selection, and lazy prompts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
import re
from threading import Lock
from typing import Callable, Iterable


class SkillCost(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class SkillRisk(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


def _coerce_level(level_type: type[IntEnum], value: IntEnum | str, field: str) -> IntEnum:
    if isinstance(value, level_type):
        return value
    if isinstance(value, str):
        try:
            return level_type[value.strip().upper()]
        except KeyError as error:
            raise ValueError(f"invalid {field}: {value}") from error
    raise TypeError(f"{field} must be a string or {level_type.__name__}")


def _normalized_terms(values: Iterable[str], field: str) -> tuple[str, ...]:
    normalized = []
    seen = set()
    for value in values:
        if not isinstance(value, str):
            raise TypeError(f"{field} entries must be strings")
        term = " ".join(value.split())
        if not term:
            raise ValueError(f"{field} entries must not be empty")
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            normalized.append(term)
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    name: str
    description: str
    triggers: tuple[str, ...] = ()
    capabilities: frozenset[str] = frozenset()
    cost: SkillCost = SkillCost.LOW
    risk: SkillRisk = SkillRisk.LOW

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise TypeError("skill name must be a string")
        name = self.name.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", name):
            raise ValueError("skill name must be a stable identifier")
        if not isinstance(self.description, str):
            raise TypeError("skill description must be a string")
        description = self.description.strip()
        if not description:
            raise ValueError("skill description must not be empty")
        triggers = _normalized_terms(self.triggers, "triggers")
        capabilities = frozenset(
            term.casefold()
            for term in _normalized_terms(self.capabilities, "capabilities")
        )
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "triggers", triggers)
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(
            self,
            "cost",
            _coerce_level(SkillCost, self.cost, "cost"),
        )
        object.__setattr__(
            self,
            "risk",
            _coerce_level(SkillRisk, self.risk, "risk"),
        )


class Skill:
    """A metadata record whose prompt is loaded only on first use."""

    def __init__(
        self,
        metadata: SkillMetadata,
        prompt_loader: Callable[[], str],
    ) -> None:
        if not isinstance(metadata, SkillMetadata):
            raise TypeError("metadata must be SkillMetadata")
        if not callable(prompt_loader):
            raise TypeError("prompt_loader must be callable")
        self.metadata = metadata
        self._prompt_loader = prompt_loader
        self._prompt: str | None = None
        self._load_lock = Lock()

    @classmethod
    def from_file(
        cls,
        metadata: SkillMetadata,
        path: str | Path,
        *,
        encoding: str = "utf-8",
    ) -> "Skill":
        prompt_path = Path(path)

        def load() -> str:
            return prompt_path.read_text(encoding=encoding)

        return cls(metadata, load)

    @property
    def prompt_loaded(self) -> bool:
        return self._prompt is not None

    def load_prompt(self, *, refresh: bool = False) -> str:
        with self._load_lock:
            if self._prompt is None or refresh:
                prompt = self._prompt_loader()
                if not isinstance(prompt, str):
                    raise TypeError("prompt_loader must return a string")
                if not prompt.strip():
                    raise ValueError("skill prompt must not be empty")
                self._prompt = prompt
            return self._prompt


@dataclass(frozen=True, slots=True)
class SkillMatch:
    skill: Skill
    matched_triggers: tuple[str, ...]


class SkillRegistry:
    """Case-insensitive registry with deterministic, policy-aware matching."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        if not isinstance(skill, Skill):
            raise TypeError("skill must be a Skill")
        key = skill.metadata.name.casefold()
        if key in self._skills:
            raise ValueError(f"skill already registered: {skill.metadata.name}")
        self._skills[key] = skill

    def unregister(self, name: str) -> Skill:
        try:
            return self._skills.pop(name.casefold())
        except KeyError as error:
            raise KeyError(f"unknown skill: {name}") from error

    def get(self, name: str) -> Skill:
        try:
            return self._skills[name.casefold()]
        except KeyError as error:
            raise KeyError(f"unknown skill: {name}") from error

    def names(self) -> tuple[str, ...]:
        return tuple(
            skill.metadata.name
            for skill in sorted(
                self._skills.values(),
                key=lambda item: item.metadata.name.casefold(),
            )
        )

    def find(
        self,
        task: str,
        *,
        required_capabilities: Iterable[str] = (),
        max_cost: SkillCost | str = SkillCost.HIGH,
        max_risk: SkillRisk | str = SkillRisk.HIGH,
    ) -> tuple[SkillMatch, ...]:
        if not isinstance(task, str):
            raise TypeError("task must be a string")
        capabilities = frozenset(
            term.casefold()
            for term in _normalized_terms(required_capabilities, "required_capabilities")
        )
        cost_limit = _coerce_level(SkillCost, max_cost, "max_cost")
        risk_limit = _coerce_level(SkillRisk, max_risk, "max_risk")
        normalized_task = " ".join(task.split()).casefold()

        matches = []
        for skill in self._skills.values():
            metadata = skill.metadata
            if metadata.cost > cost_limit or metadata.risk > risk_limit:
                continue
            if not capabilities.issubset(metadata.capabilities):
                continue
            trigger_matches = tuple(
                trigger
                for trigger in metadata.triggers
                if self._matches_trigger(normalized_task, trigger)
            )
            capability_match = bool(capabilities)
            if not trigger_matches and not capability_match:
                continue
            matches.append(SkillMatch(skill, trigger_matches))

        return tuple(
            sorted(
                matches,
                key=lambda match: (
                    -len(match.matched_triggers),
                    match.skill.metadata.cost,
                    match.skill.metadata.risk,
                    match.skill.metadata.name.casefold(),
                ),
            )
        )

    def select(
        self,
        task: str,
        *,
        required_capabilities: Iterable[str] = (),
        max_cost: SkillCost | str = SkillCost.HIGH,
        max_risk: SkillRisk | str = SkillRisk.HIGH,
    ) -> Skill | None:
        matches = self.find(
            task,
            required_capabilities=required_capabilities,
            max_cost=max_cost,
            max_risk=max_risk,
        )
        return matches[0].skill if matches else None

    def load_prompt(self, name: str, *, refresh: bool = False) -> str:
        return self.get(name).load_prompt(refresh=refresh)

    @staticmethod
    def _matches_trigger(task: str, trigger: str) -> bool:
        normalized_trigger = " ".join(trigger.split()).casefold()
        pattern = rf"(?<!\w){re.escape(normalized_trigger)}(?!\w)"
        return re.search(pattern, task) is not None


Cost = SkillCost
Risk = SkillRisk
