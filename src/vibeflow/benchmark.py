"""Deterministic model benchmarking with an explicit live-call gate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import time
from typing import Any, Callable, Mapping, Sequence

from .safety import redact_secrets


@dataclass(frozen=True)
class BenchmarkCase:
    """Representative prompt and deterministic quality-proxy terms."""

    case_id: str
    category: str
    prompt: str
    expected_terms: tuple[str, ...]

    def quality_proxy(self, response_text: str) -> float:
        normalized = response_text.casefold()
        if not self.expected_terms:
            return 1.0 if normalized.strip() else 0.0
        covered = sum(term.casefold() in normalized for term in self.expected_terms)
        return covered / len(self.expected_terms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "prompt": self.prompt,
            "expected_terms": list(self.expected_terms),
        }


DEFAULT_BENCHMARK_CASES: tuple[BenchmarkCase, ...] = (
    BenchmarkCase(
        case_id="small-fix",
        category="implementation",
        prompt=(
            "Explain and fix an off-by-one loop that indexes one item past a sequence. "
            "Return a concise patch rationale."
        ),
        expected_terms=("range", "length", "index"),
    ),
    BenchmarkCase(
        case_id="edge-tests",
        category="testing",
        prompt=(
            "List representative tests for a parser that accepts an optional integer and "
            "raises a validation error for malformed input."
        ),
        expected_terms=("empty", "boundary", "error"),
    ),
    BenchmarkCase(
        case_id="migration-plan",
        category="architecture",
        prompt=(
            "Propose a safe migration plan for replacing a synchronous storage adapter with "
            "an asynchronous one in a production service."
        ),
        expected_terms=("rollback", "observability", "risk"),
    ),
)

DEFAULT_CASES = DEFAULT_BENCHMARK_CASES


@dataclass(frozen=True)
class ModelSpec:
    """Model identifier and optional per-million-token prices."""

    name: str
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Model name must not be empty")
        for price in (self.input_cost_per_million, self.output_cost_per_million):
            if price is not None and (not math.isfinite(price) or price < 0):
                raise ValueError("Model prices must be finite and non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "input_cost_per_million": self.input_cost_per_million,
            "output_cost_per_million": self.output_cost_per_million,
        }


@dataclass(frozen=True)
class BenchmarkResponse:
    """Normalized provider response used for metrics only."""

    text: str
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("Token counts must be non-negative")


class BenchmarkStatus(str, Enum):
    """State of one model and case measurement."""

    DRY_RUN = "dry_run"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class BenchmarkRecord:
    """Metrics for one model and representative case."""

    model: str
    case_id: str
    category: str
    status: BenchmarkStatus
    quality_proxy: float | None = None
    latency_seconds: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "case_id": self.case_id,
            "category": self.category,
            "status": self.status.value,
            "quality_proxy": self.quality_proxy,
            "latency_seconds": self.latency_seconds,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "error": self.error,
        }


@dataclass(frozen=True)
class ModelSummary:
    """Aggregate benchmark metrics for tier recommendation."""

    model: str
    completed_cases: int
    average_quality_proxy: float
    average_latency_seconds: float
    average_estimated_cost_usd: float | None
    total_input_tokens: int
    total_output_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "completed_cases": self.completed_cases,
            "average_quality_proxy": self.average_quality_proxy,
            "average_latency_seconds": self.average_latency_seconds,
            "average_estimated_cost_usd": self.average_estimated_cost_usd,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
        }


@dataclass(frozen=True)
class BenchmarkReport:
    """Benchmark plan or completed live report."""

    dry_run: bool
    cases: tuple[BenchmarkCase, ...]
    models: tuple[ModelSpec, ...]
    records: tuple[BenchmarkRecord, ...]
    summaries: tuple[ModelSummary, ...]
    tier_mappings: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "live_calls_made": not self.dry_run,
            "cases": [case.to_dict() for case in self.cases],
            "models": [model.to_dict() for model in self.models],
            "records": [record.to_dict() for record in self.records],
            "summaries": [summary.to_dict() for summary in self.summaries],
            "tier_mappings": dict(self.tier_mappings),
        }


def _coerce_non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        converted = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if converted < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return converted


def _coerce_response(value: BenchmarkResponse | Mapping[str, Any]) -> BenchmarkResponse:
    if isinstance(value, BenchmarkResponse):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("Benchmark provider must return BenchmarkResponse or a mapping")

    usage = value.get("usage", {})
    usage = usage if isinstance(usage, Mapping) else {}
    text = value.get("text", value.get("content", ""))
    input_tokens = value.get("input_tokens", usage.get("input_tokens", 0))
    output_tokens = value.get("output_tokens", usage.get("output_tokens", 0))
    return BenchmarkResponse(
        text=str(text),
        input_tokens=_coerce_non_negative_int(input_tokens, "input_tokens"),
        output_tokens=_coerce_non_negative_int(output_tokens, "output_tokens"),
    )


def estimate_cost(model: ModelSpec, response: BenchmarkResponse) -> float | None:
    """Estimate request cost when both token prices are known."""

    if model.input_cost_per_million is None or model.output_cost_per_million is None:
        return None
    input_cost = response.input_tokens * model.input_cost_per_million / 1_000_000
    output_cost = response.output_tokens * model.output_cost_per_million / 1_000_000
    return input_cost + output_cost


def summarize_records(records: Sequence[BenchmarkRecord]) -> tuple[ModelSummary, ...]:
    """Aggregate only completed records, ordered by model name."""

    completed = [record for record in records if record.status is BenchmarkStatus.COMPLETED]
    summaries: list[ModelSummary] = []
    for model in sorted({record.model for record in completed}):
        model_records = [record for record in completed if record.model == model]
        quality_values = [
            record.quality_proxy for record in model_records if record.quality_proxy is not None
        ]
        latency_values = [
            record.latency_seconds for record in model_records if record.latency_seconds is not None
        ]
        cost_values = [
            record.estimated_cost_usd
            for record in model_records
            if record.estimated_cost_usd is not None
        ]
        summaries.append(
            ModelSummary(
                model=model,
                completed_cases=len(model_records),
                average_quality_proxy=sum(quality_values) / len(quality_values),
                average_latency_seconds=sum(latency_values) / len(latency_values),
                average_estimated_cost_usd=(
                    sum(cost_values) / len(cost_values) if cost_values else None
                ),
                total_input_tokens=sum(record.input_tokens or 0 for record in model_records),
                total_output_tokens=sum(record.output_tokens or 0 for record in model_records),
            )
        )
    return tuple(summaries)


def _normalized(value: float, values: Sequence[float]) -> float:
    minimum = min(values)
    maximum = max(values)
    if maximum == minimum:
        return 0.0
    return (value - minimum) / (maximum - minimum)


def recommend_tier_mappings(
    records: Sequence[BenchmarkRecord],
    *,
    minimum_cheap_quality: float = 0.6,
) -> dict[str, str]:
    """Recommend cheap, standard, and strong mappings from measured metrics."""

    if not 0 <= minimum_cheap_quality <= 1:
        raise ValueError("minimum_cheap_quality must be between zero and one")
    summaries = summarize_records(records)
    if not summaries:
        return {}

    eligible_cheap = [
        summary
        for summary in summaries
        if summary.average_quality_proxy >= minimum_cheap_quality
    ] or list(summaries)
    cheap = min(
        eligible_cheap,
        key=lambda summary: (
            summary.average_estimated_cost_usd is None,
            summary.average_estimated_cost_usd
            if summary.average_estimated_cost_usd is not None
            else math.inf,
            summary.average_latency_seconds,
            -summary.average_quality_proxy,
            summary.model,
        ),
    )
    strong = min(
        summaries,
        key=lambda summary: (
            -summary.average_quality_proxy,
            summary.average_latency_seconds,
            summary.average_estimated_cost_usd
            if summary.average_estimated_cost_usd is not None
            else math.inf,
            summary.model,
        ),
    )

    known_costs = [
        summary.average_estimated_cost_usd
        for summary in summaries
        if summary.average_estimated_cost_usd is not None
    ]
    fallback_cost = max(known_costs, default=0.0)
    costs = [
        summary.average_estimated_cost_usd
        if summary.average_estimated_cost_usd is not None
        else fallback_cost
        for summary in summaries
    ]
    latencies = [summary.average_latency_seconds for summary in summaries]

    def balance_score(summary: ModelSummary) -> tuple[float, float, str]:
        cost = (
            summary.average_estimated_cost_usd
            if summary.average_estimated_cost_usd is not None
            else fallback_cost
        )
        score = (
            summary.average_quality_proxy
            - 0.15 * _normalized(cost, costs)
            - 0.10 * _normalized(summary.average_latency_seconds, latencies)
        )
        return (-score, summary.average_latency_seconds, summary.model)

    standard = min(summaries, key=balance_score)
    return {
        "cheap": cheap.model,
        "standard": standard.model,
        "strong": strong.model,
    }


class BenchmarkRunner:
    """Run representative cases through an injected provider."""

    def __init__(
        self,
        provider: Callable[
            [str, BenchmarkCase], BenchmarkResponse | Mapping[str, Any]
        ]
        | None = None,
        *,
        cases: Sequence[BenchmarkCase] = DEFAULT_BENCHMARK_CASES,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not cases:
            raise ValueError("At least one benchmark case is required")
        case_ids = [case.case_id for case in cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("Benchmark case IDs must be unique")
        self.provider = provider
        self.cases = tuple(cases)
        self._clock = clock or time.perf_counter

    @staticmethod
    def _normalize_models(models: Sequence[ModelSpec | str]) -> tuple[ModelSpec, ...]:
        normalized = tuple(
            model if isinstance(model, ModelSpec) else ModelSpec(str(model))
            for model in models
        )
        if not normalized:
            raise ValueError("At least one model is required")
        names = [model.name for model in normalized]
        if len(set(names)) != len(names):
            raise ValueError("Model names must be unique")
        return normalized

    def run(
        self,
        models: Sequence[ModelSpec | str],
        *,
        allow_paid: bool = False,
        live: bool | None = None,
    ) -> BenchmarkReport:
        """Create a dry-run plan unless live calls are explicitly enabled."""

        normalized_models = self._normalize_models(models)
        live_enabled = allow_paid if live is None else bool(live)
        if live is not None and allow_paid and not live:
            raise ValueError("Conflicting live benchmark flags")

        if not live_enabled:
            records = tuple(
                BenchmarkRecord(
                    model=model.name,
                    case_id=case.case_id,
                    category=case.category,
                    status=BenchmarkStatus.DRY_RUN,
                )
                for model in normalized_models
                for case in self.cases
            )
            return BenchmarkReport(
                dry_run=True,
                cases=self.cases,
                models=normalized_models,
                records=records,
                summaries=(),
                tier_mappings={},
            )

        if self.provider is None:
            raise RuntimeError("Live benchmark requested without a benchmark provider")

        records: list[BenchmarkRecord] = []
        for model in normalized_models:
            for case in self.cases:
                started = self._clock()
                try:
                    response = _coerce_response(self.provider(model.name, case))
                    latency = max(0.0, self._clock() - started)
                    records.append(
                        BenchmarkRecord(
                            model=model.name,
                            case_id=case.case_id,
                            category=case.category,
                            status=BenchmarkStatus.COMPLETED,
                            quality_proxy=case.quality_proxy(response.text),
                            latency_seconds=latency,
                            input_tokens=response.input_tokens,
                            output_tokens=response.output_tokens,
                            estimated_cost_usd=estimate_cost(model, response),
                        )
                    )
                except Exception as exc:
                    latency = max(0.0, self._clock() - started)
                    records.append(
                        BenchmarkRecord(
                            model=model.name,
                            case_id=case.case_id,
                            category=case.category,
                            status=BenchmarkStatus.FAILED,
                            latency_seconds=latency,
                            error=redact_secrets(f"{type(exc).__name__}: {exc}"),
                        )
                    )

        summaries = summarize_records(records)
        mappings = recommend_tier_mappings(records)
        return BenchmarkReport(
            dry_run=False,
            cases=self.cases,
            models=normalized_models,
            records=tuple(records),
            summaries=summaries,
            tier_mappings=mappings,
        )

