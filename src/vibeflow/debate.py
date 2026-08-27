"""Bounded role debate with private histories and final judge synthesis."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

from .agent import AgentExecutor, AgentMessage, AgentRequest, AgentResult


@dataclass(frozen=True, slots=True)
class DebateRole:
    name: str
    instructions: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise TypeError("debate role name must be a string")
        if not self.name.strip():
            raise ValueError("debate role name must not be empty")
        if not isinstance(self.instructions, str):
            raise TypeError("debate role instructions must be a string")
        if not self.instructions.strip():
            raise ValueError("debate role instructions must not be empty")


DEFAULT_DEBATE_ROLES = (
    DebateRole("pragmatist", "Prefer the simplest option that works in practice."),
    DebateRole("contrarian", "Challenge assumptions and the current leading option."),
    DebateRole("edge-case-finder", "Find boundary conditions and failure modes."),
    DebateRole("security-reviewer", "Identify abuse cases and security risks."),
)


@dataclass(frozen=True, slots=True)
class DebateTurn:
    round_number: int
    role: str
    request: AgentRequest
    result: AgentResult


@dataclass(frozen=True, slots=True)
class DebateResult:
    topic: str
    rounds_completed: int
    turns: tuple[DebateTurn, ...]
    role_histories: Mapping[str, tuple[AgentMessage, ...]]
    judge_request: AgentRequest
    synthesis: AgentResult

    @property
    def final_answer(self) -> str:
        return self.synthesis.content


class DebateStrategy:
    """Coordinate role agents without mixing their conversational histories."""

    def __init__(
        self,
        executors: AgentExecutor | Mapping[str, AgentExecutor],
        *,
        judge_executor: AgentExecutor | None = None,
        roles: Sequence[DebateRole] = DEFAULT_DEBATE_ROLES,
        max_rounds: int = 3,
        judge_instructions: str = (
            "Act as an impartial judge. Synthesize the strongest supported conclusion, "
            "explicitly resolving material disagreements."
        ),
    ) -> None:
        resolved_roles = tuple(roles)
        if not resolved_roles:
            raise ValueError("at least one debate role is required")
        role_names = [role.name for role in resolved_roles]
        if len(set(role_names)) != len(role_names):
            raise ValueError("debate role names must be unique")
        if "judge" in role_names:
            raise ValueError("'judge' is reserved for final synthesis")
        if not isinstance(max_rounds, int) or isinstance(max_rounds, bool):
            raise TypeError("max_rounds must be an integer")
        if max_rounds < 1:
            raise ValueError("max_rounds must be at least one")
        if not judge_instructions.strip():
            raise ValueError("judge_instructions must not be empty")

        if isinstance(executors, AgentExecutor):
            role_executors = {role.name: executors for role in resolved_roles}
            resolved_judge = judge_executor or executors
        else:
            role_executors = dict(executors)
            missing = [name for name in role_names if name not in role_executors]
            if missing:
                raise ValueError(f"missing executors for roles: {', '.join(missing)}")
            resolved_judge = judge_executor or role_executors.get("judge")
            if resolved_judge is None:
                raise ValueError("a judge executor is required")

        if not all(
            isinstance(role_executors[role.name], AgentExecutor)
            for role in resolved_roles
        ):
            raise TypeError("every role executor must implement AgentExecutor")
        if not isinstance(resolved_judge, AgentExecutor):
            raise TypeError("judge_executor must implement AgentExecutor")

        self._roles = resolved_roles
        self._role_executors = role_executors
        self._judge_executor = resolved_judge
        self._judge_instructions = judge_instructions
        self.max_rounds = max_rounds

    @property
    def roles(self) -> tuple[DebateRole, ...]:
        return self._roles

    def run(self, topic: str, *, rounds: int | None = None) -> DebateResult:
        if not isinstance(topic, str):
            raise TypeError("topic must be a string")
        if not topic.strip():
            raise ValueError("topic must not be empty")
        round_count = self.max_rounds if rounds is None else rounds
        if not isinstance(round_count, int) or isinstance(round_count, bool):
            raise TypeError("rounds must be an integer")
        if not 1 <= round_count <= self.max_rounds:
            raise ValueError(f"rounds must be between 1 and {self.max_rounds}")

        histories: dict[str, list[AgentMessage]] = {
            role.name: [] for role in self._roles
        }
        turns: list[DebateTurn] = []

        for round_number in range(1, round_count + 1):
            prior_transcript = self._format_transcript(turns)
            for role in self._roles:
                prompt = self._build_role_prompt(
                    topic=topic,
                    role=role,
                    round_number=round_number,
                    prior_transcript=prior_transcript,
                )
                request = AgentRequest(
                    prompt=prompt,
                    role=role.name,
                    system_prompt=role.instructions,
                    history=tuple(histories[role.name]),
                    metadata={
                        "strategy": "debate",
                        "round": round_number,
                        "role": role.name,
                    },
                )
                result = self._role_executors[role.name].execute(request)
                if not isinstance(result, AgentResult):
                    raise TypeError("agent executors must return AgentResult")
                turns.append(DebateTurn(round_number, role.name, request, result))
                histories[role.name].extend(
                    (
                        AgentMessage("user", prompt),
                        AgentMessage("assistant", result.content),
                    )
                )

        judge_request = AgentRequest(
            prompt=self._build_judge_prompt(topic, turns),
            role="judge",
            system_prompt=self._judge_instructions,
            history=(),
            metadata={
                "strategy": "debate",
                "rounds": round_count,
            },
        )
        synthesis = self._judge_executor.execute(judge_request)
        if not isinstance(synthesis, AgentResult):
            raise TypeError("judge executor must return AgentResult")

        frozen_histories = MappingProxyType(
            {name: tuple(history) for name, history in histories.items()}
        )
        return DebateResult(
            topic=topic,
            rounds_completed=round_count,
            turns=tuple(turns),
            role_histories=frozen_histories,
            judge_request=judge_request,
            synthesis=synthesis,
        )

    @staticmethod
    def _build_role_prompt(
        *,
        topic: str,
        role: DebateRole,
        round_number: int,
        prior_transcript: str,
    ) -> str:
        parts = [
            f"Topic: {topic}",
            f"Round: {round_number}",
            f"Role: {role.name}",
            f"Mandate: {role.instructions}",
        ]
        if prior_transcript:
            parts.extend(("Prior completed rounds:", prior_transcript))
        else:
            parts.append("No prior arguments are available. Form an independent opening position.")
        parts.append("Give a concise argument and a clear recommendation.")
        return "\n\n".join(parts)

    def _build_judge_prompt(
        self,
        topic: str,
        turns: Sequence[DebateTurn],
    ) -> str:
        return "\n\n".join(
            (
                f"Topic: {topic}",
                "Complete debate transcript:",
                self._format_transcript(turns),
                self._judge_instructions,
                "Return the final decision and the decisive reasons.",
            )
        )

    @staticmethod
    def _format_transcript(turns: Sequence[DebateTurn]) -> str:
        return "\n".join(
            f"[round {turn.round_number} | {turn.role}] {turn.result.content}"
            for turn in turns
        )


Debate = DebateStrategy
