"""Bounded implementer, reviewer, resolver, and verifier loop."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Callable

from .budget import BudgetExceeded, BudgetLedger
from .changes import ChangeError, StructuredChangeApplier
from .context import ContextBundle, ContextManager
from .contracts import Contract
from .fcc_client import FCCClient
from .reviewer import Reviewer, ReviewResult
from .router import Router, RoutingDecision
from .verifier import Verifier
from .worker import Worker, WorkerResult


class ResolutionStatus(StrEnum):
    DONE = "done"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ResolverResult:
    status: ResolutionStatus
    iterations: int
    decision: RoutingDecision
    worker: WorkerResult | None = None
    review: ReviewResult | None = None
    verification: Any = None
    tier_history: tuple[str, ...] = ()
    blocker: str | None = None
    feedback_history: tuple[str, ...] = field(default_factory=tuple)
    budget: Any = None

    @property
    def success(self) -> bool:
        return self.status is ResolutionStatus.DONE

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "success": self.success,
            "iterations": self.iterations,
            "routing": self.decision.to_dict(),
            "tier_history": list(self.tier_history),
            "blocker": self.blocker,
            "feedback_history": list(self.feedback_history),
            "worker": None if self.worker is None else {
                "success": self.worker.success,
                "summary": self.worker.summary,
                "diff": self.worker.diff,
                "changed_files": list(self.worker.changed_files),
                "applied": self.worker.applied,
                "uncertainty": self.worker.uncertainty,
                "usage": dict(self.worker.usage),
                "request_id": self.worker.request_id,
            },
            "review": None if self.review is None else self.review.to_dict(),
            "verification": self.verification.to_dict()
            if hasattr(self.verification, "to_dict")
            else self.verification,
            "budget": None if self.budget is None else {
                "requests": self.budget.requests,
                "total_tokens": self.budget.total_tokens,
                "estimated_cost_usd": str(self.budget.estimated_cost_usd),
                "unpriced_requests": self.budget.unpriced_requests,
            },
        }


class Resolver:
    """Escalate uncertainty and reject changes until both checks are green."""

    def __init__(
        self,
        fcc_client: FCCClient | None = None,
        context_manager: ContextManager | None = None,
        *,
        worker: Worker | None = None,
        reviewer_factory: Callable[[], Reviewer] | None = None,
        verifier: Verifier | None = None,
        router: Router | None = None,
        max_iterations: int = 3,
        budget_ledger: BudgetLedger | None = None,
        change_applier: StructuredChangeApplier | None = None,
        review_model: str | None = None,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be positive")
        self.context_manager = context_manager or ContextManager()
        self.router = router or Router(self.context_manager.repo_root / ".ai" / "routing.toml")
        if worker is None:
            if fcc_client is None:
                raise ValueError("fcc_client is required when worker is not injected")
            worker = Worker(fcc_client, self.context_manager)
        if reviewer_factory is None:
            if fcc_client is None:
                raise ValueError("fcc_client is required when reviewer_factory is not injected")
            reviewer_factory = lambda: Reviewer(fcc_client)
        self.worker = worker
        self.reviewer_factory = reviewer_factory
        self.verifier = verifier or Verifier(self.context_manager.repo_root)
        self.max_iterations = max_iterations
        self.budget_ledger = budget_ledger
        self.change_applier = change_applier
        self.review_model = review_model

    def resolve(
        self,
        contract: Contract,
        decision: RoutingDecision,
        context: ContextBundle,
    ) -> ResolverResult:
        feedback: list[str] = []
        tiers: list[str] = []
        last_worker: WorkerResult | None = None
        last_review: ReviewResult | None = None
        verification: Any = None
        current = decision

        for iteration in range(1, self.max_iterations + 1):
            tiers.append(current.tier.value)
            if self.budget_ledger is not None:
                try:
                    self.budget_ledger.before_request()
                except BudgetExceeded as exc:
                    return self._budget_blocked(
                        iteration, current, tiers, feedback, last_worker, last_review, verification, exc
                    )
            last_worker = self.worker.execute(
                contract,
                current.model,
                context,
                feedback=feedback,
            )
            if self.budget_ledger is not None:
                try:
                    self.budget_ledger.record(current.model, last_worker.usage)
                except BudgetExceeded as exc:
                    return self._budget_blocked(
                        iteration, current, tiers, feedback, last_worker, last_review, verification, exc
                    )
            if not last_worker.success:
                feedback.append(last_worker.summary or "implementer failed")
                current = self.router.escalate(current, "implementer failure")
                continue
            if last_worker.uncertainty >= 0.75:
                feedback.append("Implementer reported material uncertainty.")
                current = self.router.escalate(current, "material uncertainty")
                continue

            if self.change_applier is not None:
                if last_worker.proposal is None:
                    feedback.append("Worker did not return a structured change proposal.")
                    current = self.router.escalate(current, "invalid change proposal")
                    continue
                try:
                    applied = self.change_applier.apply(last_worker.proposal)
                except (ChangeError, OSError, UnicodeError, ValueError) as exc:
                    feedback.append(f"Change proposal rejected: {exc}")
                    current = self.router.escalate(current, "unsafe or stale change proposal")
                    continue
                last_worker = replace(
                    last_worker,
                    diff=applied.diff,
                    changed_files=applied.changed_files,
                    applied=applied.applied,
                )

            verification = self.verifier.verify()
            green = self._verification_green(verification)
            if self.budget_ledger is not None:
                try:
                    self.budget_ledger.before_request()
                except BudgetExceeded as exc:
                    return self._budget_blocked(
                        iteration, current, tiers, feedback, last_worker, last_review, verification, exc
                    )
            last_review = self.reviewer_factory().review(
                contract,
                context,
                last_worker.diff,
                verification,
                self.review_model or current.model,
            )
            if self.budget_ledger is not None:
                try:
                    self.budget_ledger.record(self.review_model or current.model, last_review.usage)
                except BudgetExceeded as exc:
                    return self._budget_blocked(
                        iteration, current, tiers, feedback, last_worker, last_review, verification, exc
                    )
            if green and last_review.approved and last_worker.applied:
                return ResolverResult(
                    status=ResolutionStatus.DONE,
                    iterations=iteration,
                    decision=current,
                    worker=last_worker,
                    review=last_review,
                    verification=verification,
                    tier_history=tuple(tiers),
                    feedback_history=tuple(feedback),
                    budget=None if self.budget_ledger is None else self.budget_ledger.snapshot,
                )
            if not green:
                feedback.append("Deterministic verification is not green.")
            feedback.extend(last_review.required_changes)
            if not last_review.approved and last_review.feedback:
                feedback.append(last_review.feedback)
            if green and last_review.approved and not last_worker.applied:
                self._rollback()
                return ResolverResult(
                    status=ResolutionStatus.BLOCKED,
                    iterations=iteration,
                    decision=current,
                    worker=last_worker,
                    review=last_review,
                    verification=verification,
                    tier_history=tuple(tiers),
                    blocker="Reviewed proposal was not applied by an execution adapter.",
                    feedback_history=tuple(feedback),
                    budget=None if self.budget_ledger is None else self.budget_ledger.snapshot,
                )
            if self.change_applier is not None and self.change_applier.touched_files:
                context = self.context_manager.build_context(
                    contract,
                    self.change_applier.touched_files,
                    max_tokens=context.max_tokens,
                )
            current = self.router.escalate(current, "review or verification failure")

        self._rollback()
        return ResolverResult(
            status=ResolutionStatus.BLOCKED,
            iterations=self.max_iterations,
            decision=current,
            worker=last_worker,
            review=last_review,
            verification=verification,
            tier_history=tuple(tiers),
            blocker="Resolver iteration or escalation limit reached.",
            feedback_history=tuple(feedback),
            budget=None if self.budget_ledger is None else self.budget_ledger.snapshot,
        )

    def _budget_blocked(
        self,
        iteration: int,
        decision: RoutingDecision,
        tiers: list[str],
        feedback: list[str],
        worker: WorkerResult | None,
        review: ReviewResult | None,
        verification: Any,
        error: BudgetExceeded,
    ) -> ResolverResult:
        self._rollback()
        return ResolverResult(
            status=ResolutionStatus.BLOCKED,
            iterations=iteration,
            decision=decision,
            worker=worker,
            review=review,
            verification=verification,
            tier_history=tuple(tiers),
            blocker=str(error),
            feedback_history=tuple(feedback),
            budget=None if self.budget_ledger is None else self.budget_ledger.snapshot,
        )

    def _verification_green(self, report: Any) -> bool:
        if hasattr(self.verifier, "is_green"):
            return bool(self.verifier.is_green(report))
        if hasattr(report, "accepted"):
            return bool(report.accepted)
        if hasattr(report, "passed"):
            return bool(report.passed)
        if isinstance(report, dict) and report:
            values = []
            for result in report.values():
                if isinstance(result, tuple) and result:
                    values.append(bool(result[0]))
                elif isinstance(result, bool):
                    values.append(result)
            return bool(values) and all(values)
        return False

    def _rollback(self) -> None:
        if self.change_applier is not None:
            self.change_applier.rollback()
