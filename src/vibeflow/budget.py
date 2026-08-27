"""Configurable token pricing and non-authoritative cost estimation."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


_MILLION = Decimal(1_000_000)


def _decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a non-negative finite number")
    try:
        converted = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{field_name} must be a non-negative finite number") from None
    if not converted.is_finite() or converted < 0:
        raise ValueError(f"{field_name} must be a non-negative finite number")
    return converted


def _tokens(usage: Mapping[str, Any], primary: str, legacy: str) -> int:
    value = usage.get(primary, usage.get(legacy, 0))
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{primary} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """USD charged per one million input and output tokens."""

    input_per_million: Decimal
    output_per_million: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "input_per_million",
            _decimal(self.input_per_million, "input_per_million"),
        )
        object.__setattr__(
            self,
            "output_per_million",
            _decimal(self.output_per_million, "output_per_million"),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ModelPricing":
        input_price = next(
            (
                value[key]
                for key in (
                    "input_per_million",
                    "input_per_million_tokens",
                    "input_per_million_usd",
                    "input_per_1m_tokens",
                )
                if key in value
            ),
            None,
        )
        output_price = next(
            (
                value[key]
                for key in (
                    "output_per_million",
                    "output_per_million_tokens",
                    "output_per_million_usd",
                    "output_per_1m_tokens",
                )
                if key in value
            ),
            None,
        )
        if input_price is None or output_price is None:
            raise ValueError("Pricing requires input_per_million and output_per_million")
        return cls(input_price, output_price)


@dataclass(frozen=True, slots=True)
class CostEstimate:
    model: str
    input_tokens: int
    output_tokens: int
    input_cost: Decimal
    output_cost: Decimal
    total_cost: Decimal

    @property
    def input_cost_usd(self) -> Decimal:
        return self.input_cost

    @property
    def output_cost_usd(self) -> Decimal:
        return self.output_cost

    @property
    def total_cost_usd(self) -> Decimal:
        return self.total_cost


class PricingTable(Mapping[str, ModelPricing]):
    """Exact model-to-price mapping; empty unless supplied by the caller."""

    def __init__(
        self,
        prices: Mapping[str, ModelPricing | Mapping[str, Any]] | None = None,
    ) -> None:
        self._prices: dict[str, ModelPricing] = {}
        for model, pricing in (prices or {}).items():
            self.set(model, pricing)

    def __getitem__(self, model: str) -> ModelPricing:
        return self._prices[model]

    def __iter__(self) -> Iterator[str]:
        return iter(self._prices)

    def __len__(self) -> int:
        return len(self._prices)

    def set(self, model: str, pricing: ModelPricing | Mapping[str, Any]) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        resolved = pricing if isinstance(pricing, ModelPricing) else ModelPricing.from_mapping(pricing)
        self._prices[model] = resolved


class CostEstimator:
    """Estimate configured model costs without inventing prices for unknown models."""

    def __init__(
        self,
        pricing: PricingTable | Mapping[str, ModelPricing | Mapping[str, Any]] | None = None,
    ) -> None:
        self.pricing = pricing if isinstance(pricing, PricingTable) else PricingTable(pricing)

    def estimate(self, model: str, usage: Mapping[str, Any]) -> CostEstimate | None:
        pricing = self.pricing.get(model)
        if pricing is None:
            return None
        input_tokens = _tokens(usage, "input_tokens", "prompt_tokens")
        output_tokens = _tokens(usage, "output_tokens", "completion_tokens")
        input_cost = Decimal(input_tokens) * pricing.input_per_million / _MILLION
        output_cost = Decimal(output_tokens) * pricing.output_per_million / _MILLION
        return CostEstimate(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=input_cost + output_cost,
        )


def estimate_cost(
    model: str,
    usage: Mapping[str, Any],
    pricing: PricingTable | Mapping[str, ModelPricing | Mapping[str, Any]],
) -> Decimal | None:
    """Return only the estimated USD total, or None when pricing is unknown."""

    estimate = CostEstimator(pricing).estimate(model, usage)
    return None if estimate is None else estimate.total_cost


class BudgetExceeded(RuntimeError):
    """Raised before further model work when a configured limit is exhausted."""


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    max_requests: int | None = None
    max_total_tokens: int | None = None
    max_estimated_cost_usd: Decimal | None = None
    allow_unknown_cost: bool = True

    def __post_init__(self) -> None:
        for name in ("max_requests", "max_total_tokens"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1
            ):
                raise ValueError(f"{name} must be a positive integer or None")
        if self.max_estimated_cost_usd is not None:
            object.__setattr__(
                self,
                "max_estimated_cost_usd",
                _decimal(self.max_estimated_cost_usd, "max_estimated_cost_usd"),
            )


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    requests: int = 0
    total_tokens: int = 0
    estimated_cost_usd: Decimal = Decimal(0)
    unpriced_requests: int = 0


class BudgetLedger:
    """Track one orchestration run against an explicit budget policy."""

    def __init__(
        self,
        policy: BudgetPolicy | None = None,
        *,
        estimator: CostEstimator | None = None,
    ) -> None:
        self.policy = policy or BudgetPolicy()
        self.estimator = estimator or CostEstimator()
        self.snapshot = BudgetSnapshot()

    def before_request(self) -> None:
        maximum = self.policy.max_requests
        if maximum is not None and self.snapshot.requests >= maximum:
            raise BudgetExceeded("request budget exhausted")

    def record(self, model: str, usage: Mapping[str, Any] | None) -> BudgetSnapshot:
        input_tokens = _tokens(usage or {}, "input_tokens", "prompt_tokens")
        output_tokens = _tokens(usage or {}, "output_tokens", "completion_tokens")
        total_tokens = input_tokens + output_tokens
        estimate = self.estimator.estimate(model, usage or {})
        unpriced = int(estimate is None)
        if estimate is None and self.policy.max_estimated_cost_usd is not None:
            if not self.policy.allow_unknown_cost:
                raise BudgetExceeded(f"cost is unknown for model {model}")
            cost = Decimal(0)
        else:
            cost = Decimal(0) if estimate is None else estimate.total_cost
        next_snapshot = BudgetSnapshot(
            requests=self.snapshot.requests + 1,
            total_tokens=self.snapshot.total_tokens + total_tokens,
            estimated_cost_usd=self.snapshot.estimated_cost_usd + cost,
            unpriced_requests=self.snapshot.unpriced_requests + unpriced,
        )
        if (
            self.policy.max_total_tokens is not None
            and next_snapshot.total_tokens > self.policy.max_total_tokens
        ):
            raise BudgetExceeded("token budget exceeded")
        if (
            self.policy.max_estimated_cost_usd is not None
            and next_snapshot.estimated_cost_usd > self.policy.max_estimated_cost_usd
        ):
            raise BudgetExceeded("estimated cost budget exceeded")
        self.snapshot = next_snapshot
        return self.snapshot
