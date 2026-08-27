"""Deterministic semantic and cost-tier routing."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping
import tomllib


class Tier(StrEnum):
    CHEAP = "cheap"
    STANDARD = "standard"
    STRONG = "strong"


TIER_ORDER = (Tier.CHEAP, Tier.STANDARD, Tier.STRONG)
DEFAULT_MODELS = {
    Tier.CHEAP: "openrouter/deepseek/deepseek-v4-flash-0731",
    Tier.STANDARD: "openrouter/deepseek/deepseek-v4-pro-0813",
    Tier.STRONG: "auto:openai-codex",
}


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    tier: Tier
    model: str
    strategy: str
    review_required: bool
    score: float
    reasons: tuple[str, ...]
    escalation_count: int = 0
    max_escalations: int = 2

    @property
    def review_requirement(self) -> bool:
        return self.review_required

    @property
    def escalation_policy(self) -> str:
        return "stronger_model" if self.tier is not Tier.STRONG else "human"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier.value,
            "model": self.model,
            "strategy": self.strategy,
            "review_requirement": self.review_required,
            "escalation_policy": self.escalation_policy,
            "score": self.score,
            "reasons": list(self.reasons),
            "escalation_count": self.escalation_count,
            "max_escalations": self.max_escalations,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


class Router:
    """Map task semantics to logical tiers, never directly to providers."""

    def __init__(
        self,
        routing_config: str | Path = ".ai/routing.toml",
        *,
        model_overrides: Mapping[str, str] | None = None,
    ) -> None:
        self.routing_config_path = Path(routing_config)
        self.max_escalations = 2
        self.routing_config = self._load_config()
        for tier, model in (model_overrides or {}).items():
            if tier in {item.value for item in Tier} and isinstance(model, str) and model.strip():
                self.routing_config[tier] = model.strip()

    def _load_config(self) -> dict[str, str]:
        models = {tier.value: model for tier, model in DEFAULT_MODELS.items()}
        if not self.routing_config_path.is_file():
            return models
        try:
            with self.routing_config_path.open("rb") as handle:
                config = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            return models
        tiers = config.get("tiers", {})
        if isinstance(tiers, Mapping):
            for tier in Tier:
                value = tiers.get(tier.value, {})
                if isinstance(value, Mapping) and isinstance(value.get("model"), str):
                    models[tier.value] = value["model"].strip() or models[tier.value]
        policy = config.get("policy", {})
        if isinstance(policy, Mapping):
            self.max_escalations = max(0, min(2, int(policy.get("max_escalations", 2))))
        return models

    def model_for(self, tier: Tier | str) -> str:
        return self.routing_config[Tier(tier).value]

    def route(
        self,
        task_type: str,
        complexity: int,
        risk: str,
        expected_scope: str,
        previous_failures: int = 0,
        uncertainty: int = 0,
        verification_criticality: int = 5,
        cto_override: str | None = None,
    ) -> RoutingDecision:
        for name, value in {
            "complexity": complexity,
            "uncertainty": uncertainty,
            "verification_criticality": verification_criticality,
        }.items():
            if not 0 <= value <= 10:
                raise ValueError(f"{name} must be between 0 and 10")
        if risk not in {"low", "medium", "high"}:
            raise ValueError("risk must be low, medium, or high")
        if expected_scope not in {"small", "medium", "large"}:
            raise ValueError("expected_scope must be small, medium, or large")

        score = float(complexity)
        reasons = [f"complexity={complexity}"]
        risk_weight = {"low": 0.0, "medium": 2.0, "high": 5.0}[risk]
        scope_weight = {"small": 0.0, "medium": 1.5, "large": 3.0}[expected_scope]
        score += risk_weight + scope_weight + uncertainty * 0.4 + verification_criticality * 0.3
        if risk_weight:
            reasons.append(f"risk={risk}")
        if scope_weight:
            reasons.append(f"scope={expected_scope}")
        normalized_type = task_type.lower()
        if any(word in normalized_type for word in ("architecture", "security", "migration")):
            score += 1.5
            reasons.append("critical task type")
        elif any(word in normalized_type for word in ("docs", "format", "rename")):
            score -= 1.0
            reasons.append("bounded task type")

        tier = Tier.CHEAP if score < 5 else Tier.STRONG if score >= 11 else Tier.STANDARD
        escalations = min(max(previous_failures, 0), self.max_escalations)
        for _ in range(escalations):
            tier = self._next_tier(tier)
        if escalations:
            reasons.append(f"{escalations} prior failure escalation(s)")
        if cto_override is not None:
            tier = Tier(cto_override)
            reasons.append("Layer-1 CTO override")

        high_uncertainty = uncertainty >= 7 and complexity >= 7
        strategy = "consensus" if high_uncertainty else "review"
        if risk == "high" and high_uncertainty:
            strategy = "debate"
        review_required = task_type.lower() not in {"question", "explanation"}
        return RoutingDecision(
            tier=tier,
            model=self.model_for(tier),
            strategy=strategy,
            review_required=review_required,
            score=round(score, 2),
            reasons=tuple(reasons),
            escalation_count=escalations,
            max_escalations=self.max_escalations,
        )

    def escalate(self, decision: RoutingDecision, reason: str) -> RoutingDecision:
        if decision.escalation_count >= decision.max_escalations or decision.tier is Tier.STRONG:
            return replace(decision, reasons=decision.reasons + (f"escalation capped: {reason}",))
        tier = self._next_tier(decision.tier)
        return replace(
            decision,
            tier=tier,
            model=self.model_for(tier),
            escalation_count=decision.escalation_count + 1,
            reasons=decision.reasons + (f"escalated: {reason}",),
        )

    @staticmethod
    def _next_tier(tier: Tier) -> Tier:
        return TIER_ORDER[min(TIER_ORDER.index(tier) + 1, len(TIER_ORDER) - 1)]
