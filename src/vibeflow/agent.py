"""Backend-neutral contracts shared by Vibeflow agent strategies."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable


def _frozen_mapping(values: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(values, Mapping):
        raise TypeError("metadata must be a mapping")
    return MappingProxyType(dict(values))


@dataclass(frozen=True, slots=True)
class AgentMessage:
    """One immutable message in an agent's private history."""

    role: str
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, str):
            raise TypeError("message role must be a string")
        if not self.role.strip():
            raise ValueError("message role must not be empty")
        if not isinstance(self.content, str):
            raise TypeError("message content must be a string")


@dataclass(frozen=True, slots=True)
class AgentRequest:
    """A complete, serializable request passed to an agent executor."""

    prompt: str
    role: str = "assistant"
    system_prompt: str | None = None
    history: tuple[AgentMessage, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.prompt, str):
            raise TypeError("agent prompt must be a string")
        if not self.prompt.strip():
            raise ValueError("agent prompt must not be empty")
        if not isinstance(self.role, str):
            raise TypeError("agent role must be a string")
        if not self.role.strip():
            raise ValueError("agent role must not be empty")
        if self.system_prompt is not None and not isinstance(self.system_prompt, str):
            raise TypeError("system_prompt must be a string or None")
        history = tuple(self.history)
        if any(not isinstance(message, AgentMessage) for message in history):
            raise TypeError("history entries must be AgentMessage")
        object.__setattr__(self, "history", history)
        object.__setattr__(self, "metadata", _frozen_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class AgentUsage:
    """Optional provider-neutral usage information."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.input_tokens, int)
            or isinstance(self.input_tokens, bool)
            or not isinstance(self.output_tokens, int)
            or isinstance(self.output_tokens, bool)
        ):
            raise TypeError("token counts must be integers")
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("token counts must be non-negative")
        if self.cost is not None and (
            not isinstance(self.cost, (int, float)) or isinstance(self.cost, bool)
        ):
            raise TypeError("cost must be a number or None")
        if self.cost is not None and self.cost < 0:
            raise ValueError("cost must be non-negative")


@dataclass(frozen=True, slots=True)
class AgentResult:
    """Normalized output returned by every agent executor."""

    content: str
    decision: str | None = None
    confidence: float | None = None
    usage: AgentUsage | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise TypeError("agent result content must be a string")
        if self.decision is not None and not isinstance(self.decision, str):
            raise TypeError("decision must be a string or None")
        if self.decision is not None and not self.decision.strip():
            raise ValueError("decision must be non-empty when provided")
        if self.confidence is not None and (
            not isinstance(self.confidence, (int, float))
            or isinstance(self.confidence, bool)
        ):
            raise TypeError("confidence must be a number or None")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "metadata", _frozen_mapping(self.metadata))

    @property
    def position(self) -> str:
        """Return the structured decision, or fall back to the full content."""

        return self.decision if self.decision is not None else self.content


@runtime_checkable
class AgentExecutor(Protocol):
    """Small injection boundary implemented by real backends and test doubles."""

    def execute(self, request: AgentRequest) -> AgentResult:
        """Execute exactly one request and return a normalized result."""
