"""Layer-1 CTO orchestration for contracts, routing, context, and resolution."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
import time
from typing import Any, Callable, Iterable
import uuid

from .agent_execution import FCCAgentExecutor
from .budget import BudgetLedger
from .consensus import ConsensusStrategy
from .context import ContextBundle, ContextItem, ContextManager
from .contracts import Ambiguity, Contract, Risk, contract_from_request
from .decomposition import TaskDecomposer, TaskGraph
from .debate import DebateStrategy
from .fcc_client import FCCClient
from .resolver import Resolver, ResolverResult
from .router import Router, RoutingDecision
from .safety import SafetyGuard, SafetyViolation, redact_secrets
from .skills import SkillRegistry, SkillRisk, load_repository_skills
from .state import TaskState, TaskStateStore
from .telemetry import Telemetry


class TaskStatus(StrEnum):
    NEEDS_APPROVAL = "needs-approval"
    PLANNED = "planned"
    DONE = "done"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class TaskPlan:
    task_id: str
    contract: Contract
    routing: RoutingDecision
    context: ContextBundle
    graph: TaskGraph
    selected_skills: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "contract": self.contract.to_dict(),
            "routing": self.routing.to_dict(),
            "context_tokens": self.context.estimated_tokens,
            "context_items": [item.name for item in self.context.items],
            "subtasks": [unit.unit_id for unit in self.graph.topological_order()],
            "skills": list(self.selected_skills),
            "approval_questions": self.contract.reverse_questions(),
        }


@dataclass(frozen=True, slots=True)
class TaskResult:
    status: TaskStatus
    plan: TaskPlan
    duration_seconds: float
    resolution: ResolverResult | None = None
    blocker: str | None = None
    research: Any = None

    @property
    def success(self) -> bool:
        return self.status is TaskStatus.DONE

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "success": self.success,
            "duration_seconds": self.duration_seconds,
            "blocker": self.blocker,
            "research": None if self.research is None else self.research.to_dict(),
            **self.plan.to_dict(),
            "resolution": None if self.resolution is None else self.resolution.to_dict(),
        }


class Orchestrator:
    """Own semantic orchestration while FCC owns provider execution concerns."""

    def __init__(
        self,
        fcc_client: FCCClient | None = None,
        context_manager: ContextManager | None = None,
        telemetry_dir: Path | None = None,
        *,
        telemetry: Telemetry | None = None,
        router: Router | None = None,
        resolver_factory: Callable[[TaskPlan], Resolver] | None = None,
        safety_policy: Any = None,
        decomposer: TaskDecomposer | None = None,
        strategy_executor_factory: Callable[[str], Any] | None = None,
        state_store: TaskStateStore | None = None,
        skill_registry: SkillRegistry | None = None,
        budget_ledger_factory: Callable[[TaskPlan], BudgetLedger] | None = None,
        max_resolver_iterations: int = 3,
    ) -> None:
        self.context_manager = context_manager or ContextManager()
        self.fcc_client = fcc_client or FCCClient()
        self.router = router or Router(self.context_manager.repo_root / ".ai" / "routing.toml")
        self.telemetry_dir = telemetry_dir or self.context_manager.repo_root / ".vibeflow"
        self._telemetry = telemetry
        self.resolver_factory = resolver_factory
        self.safety_policy = safety_policy or SafetyGuard(self.context_manager.repo_root)
        self.decomposer = decomposer or TaskDecomposer()
        self.strategy_executor_factory = strategy_executor_factory
        self.state_store = state_store or TaskStateStore(
            self.context_manager.repo_root / ".ai" / "state.json"
        )
        self.skill_registry = skill_registry or load_repository_skills(
            self.context_manager.repo_root
        ).registry
        self.budget_ledger_factory = budget_ledger_factory
        self.max_resolver_iterations = max_resolver_iterations

    def plan_task(
        self,
        task_goal: str,
        *,
        task_description: str | None = None,
        context_files: Iterable[str] = (),
        acceptance_criteria: Iterable[str] | None = None,
        constraints: Iterable[str] | None = None,
        non_goals: Iterable[str] | None = None,
        failure_conditions: Iterable[str] | None = None,
        risk: Risk | str = Risk.LOW,
        ambiguity: Ambiguity | str = Ambiguity.LOW,
        task_type: str = "implementation",
        expected_scope: str = "small",
        complexity: int = 4,
        uncertainty: int = 0,
        verification_criticality: int = 5,
        previous_failures: int = 0,
        cto_override: str | None = None,
        context_budget: int = 8_000,
        subtasks: Iterable[dict[str, Any]] | None = None,
        selected_skills: Iterable[str] = (),
    ) -> TaskPlan:
        files = list(context_files)
        requested_skills: list[str] = []
        requested_risk = SkillRisk.LOW
        for name in selected_skills:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Selected skill names must be non-empty strings")
            skill = self.skill_registry.get(name.strip())
            requested_skills.append(skill.metadata.name)
            requested_risk = max(requested_risk, skill.metadata.risk)
        configured_risk = risk if isinstance(risk, Risk) else Risk(risk)
        effective_risk = Risk[
            max(
                SkillRisk[configured_risk.name],
                requested_risk,
            ).name
        ]
        contract = contract_from_request(
            task_goal,
            description=task_description,
            acceptance_criteria=acceptance_criteria,
            constraints=constraints,
            non_goals=non_goals,
            failure_conditions=failure_conditions,
            risk=effective_risk,
            ambiguity=ambiguity,
            task_type=task_type,
            expected_scope=expected_scope,
            active_files=files,
        )
        routing = self.router.route(
            task_type=task_type,
            complexity=complexity,
            risk=contract.risk.value,
            expected_scope=expected_scope,
            previous_failures=previous_failures,
            uncertainty=uncertainty,
            verification_criticality=verification_criticality,
            cto_override=cto_override,
        )
        context = self.context_manager.build_context(contract, files, max_tokens=context_budget)
        graph = self.decomposer.decompose(contract, subtasks)
        automatic_skills = tuple(
            match.skill.metadata.name
            for match in self.skill_registry.find(
                contract.goal,
                max_risk=contract.risk.value,
            )
        )
        skills = tuple(dict.fromkeys((*requested_skills, *automatic_skills)))
        return TaskPlan(str(uuid.uuid4()), contract, routing, context, graph, skills)

    def execute_task(
        self,
        task_goal: str,
        task_description: str | None = None,
        context_files: Iterable[str] = (),
        *,
        approved: bool = False,
        dry_run: bool = False,
        **plan_options: Any,
    ) -> TaskResult:
        started = time.monotonic()
        plan = self.plan_task(
            task_goal,
            task_description=task_description,
            context_files=context_files,
            **plan_options,
        )
        safety_error = self._safety_error(plan)
        if safety_error:
            result = TaskResult(
                TaskStatus.BLOCKED,
                plan,
                time.monotonic() - started,
                blocker=safety_error,
            )
            self._save_state(result)
            return result
        if plan.contract.requires_user_approval() and not approved:
            result = TaskResult(
                TaskStatus.NEEDS_APPROVAL,
                plan,
                time.monotonic() - started,
                blocker="Contract requires explicit approval before execution.",
            )
            self._save_state(result)
            return result
        if dry_run:
            return TaskResult(TaskStatus.PLANNED, plan, time.monotonic() - started)

        self._save_running(plan)
        try:
            plan = self.prepare_skills(plan)
            plan = self.prepare_strategy(plan)
            resolver = self._resolver(plan)
            resolution = resolver.resolve(plan.contract, plan.routing, plan.context)
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            result = TaskResult(
                TaskStatus.BLOCKED,
                plan,
                time.monotonic() - started,
                blocker=redact_secrets(
                    f"{type(exc).__name__} during orchestration: {exc}"
                ),
            )
            self._record(result)
            self._save_state(result)
            return result
        status = TaskStatus.DONE if resolution.success else TaskStatus.BLOCKED
        result = TaskResult(
            status,
            plan,
            time.monotonic() - started,
            resolution=resolution,
            blocker=resolution.blocker,
        )
        self._record(result)
        self._save_state(result)
        return result

    def _resolver(self, plan: TaskPlan) -> Resolver:
        if self.resolver_factory is not None:
            return self.resolver_factory(plan)
        return Resolver(
            self.fcc_client,
            self.context_manager,
            router=self.router,
            max_iterations=self.max_resolver_iterations,
            budget_ledger=(
                None if self.budget_ledger_factory is None
                else self.budget_ledger_factory(plan)
            ),
        )

    def prepare_strategy(self, plan: TaskPlan) -> TaskPlan:
        if plan.routing.strategy not in {"consensus", "debate"}:
            return plan
        factory = self.strategy_executor_factory or (
            lambda model: FCCAgentExecutor(self.fcc_client, model)
        )
        executor = factory(plan.routing.model)
        prompt = (
            f"Goal: {plan.contract.goal}\n"
            f"Acceptance criteria: {plan.contract.acceptance_criteria}\n"
            f"Failure conditions: {plan.contract.failure_conditions}"
        )
        if plan.routing.strategy == "consensus":
            result = ConsensusStrategy(executor, agent_count=3).run(
                prompt,
                uncertainty=1.0,
                value=1.0,
            )
            strategy_text = (
                f"Consensus: {result.consensus or '(none)'}\n"
                f"Disagreements: {list(result.disagreements)}\n"
                f"Outliers: {[item.agent_id for item in result.outliers]}"
            )
        else:
            result = DebateStrategy(executor, max_rounds=2).run(prompt, rounds=2)
            strategy_text = result.final_answer
        context = ContextBundle(
            items=[
                *plan.context.items,
                ContextItem(
                    name=f"{plan.routing.strategy}-synthesis",
                    content=strategy_text,
                    priority=94,
                    kind="strategy",
                ),
            ],
            max_tokens=plan.context.max_tokens,
        ).trim()
        return replace(plan, context=context)

    def prepare_skills(self, plan: TaskPlan) -> TaskPlan:
        if not plan.selected_skills:
            return plan
        items = list(plan.context.items)
        for name in plan.selected_skills:
            items.append(ContextItem(
                name=name,
                content=self.skill_registry.load_prompt(name),
                priority=93,
                kind="skill",
            ))
        return replace(
            plan,
            context=ContextBundle(items, plan.context.max_tokens).trim(),
        )

    def _safety_error(self, plan: TaskPlan) -> str | None:
        check = getattr(self.safety_policy, "check_contract", None)
        if check is not None:
            result = check(plan.contract, self.context_manager.repo_root)
            if result not in {None, True}:
                return str(result)
        validate_path = getattr(self.safety_policy, "validate_path", None)
        if validate_path is None:
            return None
        try:
            for file_path in plan.contract.active_files:
                validate_path(file_path)
        except (OSError, SafetyViolation, ValueError) as exc:
            return str(exc)
        return None

    def _record(self, result: TaskResult) -> None:
        resolution = result.resolution
        worker = resolution.worker if resolution else None
        try:
            telemetry = self._telemetry or Telemetry(self.telemetry_dir)
            self._telemetry = telemetry
            telemetry.log_event(
                task_id=result.plan.task_id,
                agent_role="orchestrator",
                model=result.plan.routing.model,
                tier=result.plan.routing.tier.value,
                usage=None if worker is None else worker.usage,
                duration=result.duration_seconds,
                request_id=None if worker is None else worker.request_id,
                success=result.success,
                strategy=result.plan.routing.strategy,
                escalation_count=0 if resolution is None else resolution.decision.escalation_count,
                error_type=None if result.success else "TaskBlocked",
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            return

    def _save_running(self, plan: TaskPlan) -> None:
        try:
            self.state_store.save(TaskState(
                task_id=plan.task_id,
                status="running",
                goal=plan.contract.goal,
                tier=plan.routing.tier.value,
                strategy=plan.routing.strategy,
                updated_at=time.time(),
            ))
        except OSError:
            return

    def _save_state(self, result: TaskResult) -> None:
        try:
            self.state_store.save(TaskState(
                task_id=result.plan.task_id,
                status=result.status.value,
                goal=result.plan.contract.goal,
                tier=result.plan.routing.tier.value,
                strategy=result.plan.routing.strategy,
                updated_at=time.time(),
                blocker=result.blocker,
            ))
        except OSError:
            return
