"""Daily-use autonomous coding runner with isolated reviewed promotion."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time
from typing import Iterable

from .context import ContextManager
from .contracts import Ambiguity, Risk
from .fcc_client import FCCClient
from .model_selection import resolve_tier_models
from .orchestrator import Orchestrator, TaskResult, TaskStatus
from .resolver import ResolutionStatus, Resolver, ResolverResult
from .router import Router
from .verifier import Verifier
from .worker import Worker
from .workspace import IsolatedWorkspace, WorkspaceError


class AutonomousRunner:
    """Plan, route, implement, verify, review, resolve, and safely promote."""

    def __init__(
        self,
        repo_root: str | Path,
        *,
        fcc_client: FCCClient | None = None,
        max_iterations: int = 3,
    ) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.fcc_client = fcc_client or FCCClient()
        self.max_iterations = max_iterations

    def run(
        self,
        goal: str,
        *,
        context_files: Iterable[str] = (),
        approved: bool = False,
        selected_skills: Iterable[str] = (),
    ) -> TaskResult:
        started = time.monotonic()
        routing_path = self.repo_root / ".ai" / "routing.toml"
        live_models = self.fcc_client.list_models()
        resolved_models = resolve_tier_models(routing_path, live_models)
        router = Router(routing_path, model_overrides=resolved_models)
        context_manager = ContextManager(self.repo_root)
        orchestrator = Orchestrator(
            self.fcc_client,
            context_manager,
            router=router,
            max_resolver_iterations=self.max_iterations,
        )
        plan_options = _infer_plan_options(goal)
        plan = orchestrator.plan_task(
            goal,
            context_files=context_files,
            selected_skills=selected_skills,
            **plan_options,
        )
        if plan.contract.requires_user_approval() and not approved:
            return TaskResult(
                TaskStatus.NEEDS_APPROVAL,
                plan,
                time.monotonic() - started,
                blocker="Contract requires explicit approval. Re-run with --approve after reviewing the plan.",
            )

        try:
            with IsolatedWorkspace(self.repo_root) as workspace:
                workspace_context_manager = ContextManager(workspace.path)
                context = workspace_context_manager.build_context(
                    plan.contract,
                    context_files,
                )
                plan = replace(plan, context=context)
                plan = orchestrator.prepare_skills(plan)
                plan = orchestrator.prepare_strategy(plan)
                resolver = Resolver(
                    self.fcc_client,
                    workspace_context_manager,
                    worker=Worker(self.fcc_client, workspace_context_manager),
                    verifier=Verifier(workspace.path),
                    router=router,
                    max_iterations=self.max_iterations,
                    change_applier=workspace.applier,
                    review_model=resolved_models["strong"],
                )
                resolution = resolver.resolve(plan.contract, plan.routing, plan.context)
                if not resolution.success:
                    return TaskResult(
                        TaskStatus.BLOCKED,
                        plan,
                        time.monotonic() - started,
                        resolution=resolution,
                        blocker=resolution.blocker,
                    )
                promoted = workspace.promote()
                worker = replace(
                    resolution.worker,
                    diff=promoted.diff,
                    changed_files=promoted.changed_files,
                    applied=True,
                )
                resolution = replace(resolution, worker=worker)
        except (OSError, RuntimeError, TypeError, ValueError, WorkspaceError) as exc:
            blocked = ResolverResult(
                ResolutionStatus.BLOCKED,
                0,
                plan.routing,
                blocker=f"Safe workspace or promotion failed: {exc}",
            )
            return TaskResult(
                TaskStatus.BLOCKED,
                plan,
                time.monotonic() - started,
                resolution=blocked,
                blocker=blocked.blocker,
            )
        return TaskResult(
            TaskStatus.DONE,
            plan,
            time.monotonic() - started,
            resolution=resolution,
        )


def _infer_plan_options(goal: str) -> dict[str, object]:
    normalized = goal.lower()
    high_risk_terms = (
        "production",
        "deploy",
        "database migration",
        "schema migration",
        "authentication",
        "authorization",
        "payment",
        "delete data",
        "rotate key",
        "credential",
    )
    risk = Risk.HIGH if any(term in normalized for term in high_risk_terms) else Risk.LOW
    if any(term in normalized for term in ("architecture", "security", "migration")):
        task_type = "architecture" if "architecture" in normalized else "security"
    elif any(term in normalized for term in ("document", "readme", "docs")):
        task_type = "docs"
    elif any(term in normalized for term in ("bug", "fix", "error", "failure")):
        task_type = "bugfix"
    else:
        task_type = "implementation"
    expected_scope = "large" if any(term in normalized for term in ("entire repo", "whole repo", "large refactor")) else "small"
    complexity = 8 if expected_scope == "large" else 4
    return {
        "risk": risk,
        "ambiguity": Ambiguity.LOW,
        "task_type": task_type,
        "expected_scope": expected_scope,
        "complexity": complexity,
    }
