"""Deterministic, independently prompted multi-agent consensus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
import unicodedata

from .agent import AgentExecutor, AgentRequest, AgentResult


DEFAULT_PROMPT_VARIATIONS = (
    "Solve the task independently. State one clear final decision and its strongest evidence.",
    "Re-evaluate the task from first principles. Prefer falsifiable evidence over assumptions.",
    "Look for edge cases before choosing one clear final decision.",
)


def _validate_score(name: str, value: float) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{name} must be a number")
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")


def normalize_position(value: str) -> str:
    """Canonicalize a position without interpreting its meaning."""

    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


@dataclass(frozen=True, slots=True)
class ConsensusPolicy:
    """Cost gate for deciding when redundant agent calls are justified."""

    uncertainty_threshold: float = 0.7
    value_threshold: float = 0.8
    trigger_mode: str = "either"

    def __post_init__(self) -> None:
        _validate_score("uncertainty_threshold", self.uncertainty_threshold)
        _validate_score("value_threshold", self.value_threshold)
        if self.trigger_mode not in {"either", "both"}:
            raise ValueError("trigger_mode must be 'either' or 'both'")

    def should_run(self, *, uncertainty: float, value: float) -> bool:
        _validate_score("uncertainty", uncertainty)
        _validate_score("value", value)
        high_uncertainty = uncertainty >= self.uncertainty_threshold
        high_value = value >= self.value_threshold
        if self.trigger_mode == "both":
            return high_uncertainty and high_value
        return high_uncertainty or high_value

    def reason(self, *, uncertainty: float, value: float) -> str:
        if not self.should_run(uncertainty=uncertainty, value=value):
            return "below-threshold"
        reasons = []
        if uncertainty >= self.uncertainty_threshold:
            reasons.append("high-uncertainty")
        if value >= self.value_threshold:
            reasons.append("high-value")
        return "+".join(reasons)


@dataclass(frozen=True, slots=True)
class ConsensusResponse:
    agent_id: str
    request: AgentRequest
    result: AgentResult


@dataclass(frozen=True, slots=True)
class ConsensusPosition:
    value: str
    normalized_value: str
    voters: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.voters)


@dataclass(frozen=True, slots=True)
class ConsensusResult:
    triggered: bool
    reason: str
    responses: tuple[ConsensusResponse, ...] = ()
    positions: tuple[ConsensusPosition, ...] = ()
    consensus: str | None = None
    disagreements: tuple[str, ...] = ()
    outliers: tuple[ConsensusResponse, ...] = ()

    @property
    def agreement_ratio(self) -> float:
        if not self.responses or not self.positions:
            return 0.0
        return self.positions[0].count / len(self.responses)


class ConsensusStrategy:
    """Run isolated requests and aggregate exact declared positions."""

    def __init__(
        self,
        executors: AgentExecutor | Sequence[AgentExecutor],
        *,
        agent_count: int | None = None,
        policy: ConsensusPolicy | None = None,
        prompt_variations: Sequence[str] = DEFAULT_PROMPT_VARIATIONS,
        minimum_agreement: int | None = None,
    ) -> None:
        if isinstance(executors, AgentExecutor):
            count = 3 if agent_count is None else agent_count
            resolved_executors = (executors,) * count
        else:
            resolved_executors = tuple(executors)
            if agent_count is not None and agent_count != len(resolved_executors):
                raise ValueError("agent_count must match the number of executors")

        if len(resolved_executors) < 2:
            raise ValueError("consensus requires at least two agents")
        if not all(isinstance(executor, AgentExecutor) for executor in resolved_executors):
            raise TypeError("every executor must implement AgentExecutor")

        variations = tuple(prompt_variations)
        if not variations or any(not variation.strip() for variation in variations):
            raise ValueError("prompt_variations must contain non-empty prompts")

        default_agreement = len(resolved_executors) // 2 + 1
        required = default_agreement if minimum_agreement is None else minimum_agreement
        if not 2 <= required <= len(resolved_executors):
            raise ValueError("minimum_agreement must be between 2 and agent count")

        self._executors = resolved_executors
        self._variations = variations
        self.policy = policy or ConsensusPolicy()
        self.minimum_agreement = required

    def should_run(self, *, uncertainty: float, value: float) -> bool:
        return self.policy.should_run(uncertainty=uncertainty, value=value)

    def run(
        self,
        prompt: str,
        *,
        uncertainty: float,
        value: float,
    ) -> ConsensusResult:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")

        reason = self.policy.reason(uncertainty=uncertainty, value=value)
        if reason == "below-threshold":
            return ConsensusResult(triggered=False, reason=reason)

        responses = []
        for index, executor in enumerate(self._executors):
            agent_id = f"consensus-agent-{index + 1}"
            variation = self._variations[index % len(self._variations)]
            independent_prompt = (
                f"{prompt}\n\n"
                f"Independent pass {index + 1}: {variation}"
            )
            request = AgentRequest(
                prompt=independent_prompt,
                role=agent_id,
                history=(),
                metadata={
                    "strategy": "consensus",
                    "agent_index": index,
                },
            )
            result = executor.execute(request)
            if not isinstance(result, AgentResult):
                raise TypeError("agent executors must return AgentResult")
            responses.append(ConsensusResponse(agent_id, request, result))

        return self._aggregate(tuple(responses), reason)

    def _aggregate(
        self,
        responses: tuple[ConsensusResponse, ...],
        reason: str,
    ) -> ConsensusResult:
        grouped: dict[str, list[ConsensusResponse]] = {}
        representatives: dict[str, str] = {}
        for response in responses:
            position = response.result.position
            key = normalize_position(position)
            grouped.setdefault(key, []).append(response)
            representatives.setdefault(key, position.strip())

        ordered_keys = sorted(grouped, key=lambda key: (-len(grouped[key]), key))
        positions = tuple(
            ConsensusPosition(
                value=representatives[key],
                normalized_value=key,
                voters=tuple(response.agent_id for response in grouped[key]),
            )
            for key in ordered_keys
        )

        winning_key = ordered_keys[0]
        has_consensus = len(grouped[winning_key]) >= self.minimum_agreement
        consensus = representatives[winning_key] if has_consensus else None
        disagreement_keys = (
            ordered_keys[1:] if has_consensus else ordered_keys
        )
        disagreements = tuple(representatives[key] for key in disagreement_keys)

        dominant_cluster_exists = len(grouped[winning_key]) > 1
        outlier_ids = {
            group[0].agent_id
            for key, group in grouped.items()
            if dominant_cluster_exists and key != winning_key and len(group) == 1
        }
        outliers = tuple(
            response for response in responses if response.agent_id in outlier_ids
        )

        return ConsensusResult(
            triggered=True,
            reason=reason,
            responses=responses,
            positions=positions,
            consensus=consensus,
            disagreements=disagreements,
            outliers=outliers,
        )


Consensus = ConsensusStrategy

