"""Reusable skill metadata, deterministic selection, and lazy prompts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
import json
import re
from threading import Lock
from typing import Callable, Iterable

from .safety import SafetyViolation, redact_secrets, validate_repo_scope


MAX_SKILL_CHARACTERS = 200_000


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

    @classmethod
    def from_document(cls, path: str | Path) -> "Skill":
        document_path = Path(path)
        metadata, body = parse_skill_document(document_path)
        return cls(metadata, lambda: body)

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

    def skills(self) -> tuple[Skill, ...]:
        return tuple(self.get(name) for name in self.names())

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


@dataclass(frozen=True, slots=True)
class SkillCatalog:
    registry: SkillRegistry
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "items": [skill_metadata_dict(skill.metadata) for skill in self.registry.skills()],
            "errors": list(self.errors),
        }


def skill_metadata_dict(metadata: SkillMetadata) -> dict[str, object]:
    return {
        "name": metadata.name,
        "description": metadata.description,
        "triggers": list(metadata.triggers),
        "capabilities": sorted(metadata.capabilities),
        "cost": metadata.cost.name.lower(),
        "risk": metadata.risk.name.lower(),
    }


def parse_skill_document(path: str | Path) -> tuple[SkillMetadata, str]:
    document_path = Path(path)
    if document_path.is_symlink():
        raise SafetyViolation("Skill instructions must not be a symbolic link")
    try:
        document = document_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Skill instructions must be UTF-8 text") from exc
    if not document.strip():
        raise ValueError("Skill instructions must not be empty")
    if len(document) > MAX_SKILL_CHARACTERS:
        raise ValueError(
            f"Skill exceeds {MAX_SKILL_CHARACTERS:,} characters"
        )
    if redact_secrets(document) != document:
        raise SafetyViolation("Skill appears to contain a credential or secret")
    metadata_values, body = _split_skill_document(document)
    name = _metadata_scalar(metadata_values, "name")
    description = _metadata_scalar(metadata_values, "description")
    triggers = _metadata_list(metadata_values, "triggers")
    if not triggers:
        triggers = (name.replace("-", " ").replace("_", " "),)
    metadata = SkillMetadata(
        name=name,
        description=description,
        triggers=triggers,
        capabilities=frozenset(_metadata_list(metadata_values, "capabilities")),
        cost=_metadata_scalar(metadata_values, "cost", default="low"),
        risk=_metadata_scalar(metadata_values, "risk", default="low"),
    )
    if not body.strip():
        raise ValueError("Skill body must contain reusable instructions")
    return metadata, body.strip() + "\n"


def load_repository_skills(repo_root: str | Path) -> SkillCatalog:
    root = Path(repo_root).expanduser().resolve()
    skills_root = root / ".ai" / "skills"
    registry = SkillRegistry()
    errors: list[str] = []
    if not skills_root.is_dir():
        return SkillCatalog(registry)
    candidates = sorted(skills_root.glob("*/SKILL.md"))
    candidates.extend(sorted(skills_root.glob("*.md")))
    for path in candidates:
        try:
            resolved = validate_repo_scope(
                root,
                path,
                allow_protected=True,
            )
            registry.register(Skill.from_document(resolved))
        except (OSError, SafetyViolation, TypeError, ValueError) as exc:
            errors.append(f"{path.relative_to(root).as_posix()}: {exc}")
    return SkillCatalog(registry, tuple(errors))


class RepositorySkillStore:
    """Create and import instruction-only skills inside one target repository."""

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        if not self.repo_root.is_dir():
            raise SafetyViolation("Repository root is not a directory")
        self.skills_root = validate_repo_scope(
            self.repo_root,
            ".ai/skills",
            allow_protected=True,
        )

    def catalog(self) -> SkillCatalog:
        return load_repository_skills(self.repo_root)

    def create(
        self,
        *,
        name: str,
        description: str,
        instructions: str,
        triggers: Iterable[str] = (),
        capabilities: Iterable[str] = (),
        cost: SkillCost | str = SkillCost.LOW,
        risk: SkillRisk | str = SkillRisk.LOW,
    ) -> SkillMetadata:
        normalized_triggers = tuple(triggers) or (
            name.replace("-", " ").replace("_", " "),
        )
        metadata = SkillMetadata(
            name=name,
            description=description,
            triggers=normalized_triggers,
            capabilities=frozenset(capabilities),
            cost=cost,
            risk=risk,
        )
        body = instructions.strip()
        if not body:
            raise ValueError("Skill instructions must not be empty")
        if len(body) > MAX_SKILL_CHARACTERS:
            raise ValueError(
                f"Skill exceeds {MAX_SKILL_CHARACTERS:,} characters"
            )
        if redact_secrets(body) != body:
            raise SafetyViolation("Skill appears to contain a credential or secret")
        self._write(metadata, body)
        return metadata

    def import_from(self, source: str | Path) -> SkillMetadata:
        source_path = Path(source).expanduser()
        if source_path.is_symlink():
            raise SafetyViolation("Skill imports cannot use symbolic links")
        document_path = source_path / "SKILL.md" if source_path.is_dir() else source_path
        if document_path.name != "SKILL.md" or not document_path.is_file():
            raise ValueError("Choose a folder containing a SKILL.md file")
        if document_path.is_symlink():
            raise SafetyViolation("Skill instructions must not be a symbolic link")
        metadata, body = parse_skill_document(document_path)
        self._write(metadata, body)
        return metadata

    def remove(self, name: str) -> None:
        metadata = self.catalog().registry.get(name).metadata
        directory = validate_repo_scope(
            self.repo_root,
            self.skills_root / _skill_slug(metadata.name),
            allow_protected=True,
        )
        document = directory / "SKILL.md"
        if document.is_symlink() or not document.is_file():
            raise SafetyViolation("Skill instructions are missing or unsafe")
        if any(path.name != "SKILL.md" for path in directory.iterdir()):
            raise SafetyViolation(
                "Skill folder contains unsupported extra files and was not removed"
            )
        document.unlink()
        directory.rmdir()

    def _write(self, metadata: SkillMetadata, body: str) -> None:
        skill_directory = validate_repo_scope(
            self.repo_root,
            self.skills_root / _skill_slug(metadata.name),
            allow_protected=True,
        )
        document_path = skill_directory / "SKILL.md"
        if document_path.exists():
            raise ValueError(f"Skill already exists: {metadata.name}")
        skill_directory.mkdir(parents=True, exist_ok=False)
        try:
            document_path.write_text(
                _render_skill_document(metadata, body),
                encoding="utf-8",
            )
        except Exception:
            try:
                skill_directory.rmdir()
            except OSError:
                pass
            raise


def _split_skill_document(document: str) -> tuple[dict[str, object], str]:
    normalized = document.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        raise ValueError("SKILL.md must start with metadata between --- markers")
    end = normalized.find("\n---\n", 4)
    if end < 0:
        raise ValueError("SKILL.md metadata is missing its closing --- marker")
    metadata_text = normalized[4:end]
    body = normalized[end + 5 :]
    values: dict[str, object] = {}
    current_list: str | None = None
    for raw_line in metadata_text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("-") and current_list:
            if current_list != "__ignored__":
                values.setdefault(current_list, [])
                assert isinstance(values[current_list], list)
                values[current_list].append(_decode_scalar(stripped[1:].strip()))
            continue
        if ":" not in raw_line:
            raise ValueError(f"Invalid skill metadata line: {stripped}")
        key, raw_value = raw_line.split(":", 1)
        key = key.strip().casefold()
        if key not in {"name", "description", "triggers", "capabilities", "cost", "risk"}:
            current_list = "__ignored__" if not raw_value.strip() else None
            continue
        raw_value = raw_value.strip()
        if not raw_value:
            values[key] = []
            current_list = key
        else:
            values[key] = _decode_scalar(raw_value)
            current_list = None
    return values, body


def _decode_scalar(value: str) -> str:
    if value.startswith('"'):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid quoted skill metadata") from exc
        if not isinstance(decoded, str):
            raise ValueError("Skill metadata values must be strings")
        return decoded
    return value.strip("'")


def _metadata_scalar(
    values: dict[str, object],
    key: str,
    *,
    default: str | None = None,
) -> str:
    value = values.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Skill metadata requires {key}")
    return value.strip()


def _metadata_list(values: dict[str, object], key: str) -> tuple[str, ...]:
    value = values.get(key, ())
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(item.strip() for item in value if item.strip())
    if value is None or value == ():
        return ()
    raise ValueError(f"Skill metadata {key} must be a list of strings")


def _skill_slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-").lower()
    if not slug:
        raise ValueError("Skill name cannot create a safe folder name")
    return slug


def _render_skill_document(metadata: SkillMetadata, body: str) -> str:
    lines = [
        "---",
        f"name: {json.dumps(metadata.name)}",
        f"description: {json.dumps(metadata.description)}",
        "triggers:",
        *(f"  - {json.dumps(trigger)}" for trigger in metadata.triggers),
        "capabilities:",
        *(f"  - {json.dumps(capability)}" for capability in sorted(metadata.capabilities)),
        f"cost: {metadata.cost.name.lower()}",
        f"risk: {metadata.risk.name.lower()}",
        "---",
        "",
        body.strip(),
        "",
    ]
    return "\n".join(lines)


Cost = SkillCost
Risk = SkillRisk
