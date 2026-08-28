"""Daily-use autonomous coding runner with isolated reviewed promotion."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time
from typing import Iterable

from .context import ContextBundle, ContextItem, ContextManager
from .contracts import Ambiguity, Risk
from .fcc_client import FCCClient
from .model_selection import resolve_research_model, resolve_tier_models
from .orchestrator import Orchestrator, TaskPlan, TaskResult, TaskStatus
from .research import OpenRouterResearcher, ResearchError, ResearchResult
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
        plan_options = _infer_plan_options(goal)
        needs_live_research = _requires_live_web_research(goal)
        needs_code_changes = _requires_code_changes(goal)
        resolved_models = None
        research_model = None
        research_max_results = 8
        if needs_live_research:
            live_models = self.fcc_client.list_models()
            research_model, research_max_results = resolve_research_model(
                routing_path,
                live_models,
            )
            if needs_code_changes:
                resolved_models = resolve_tier_models(routing_path, live_models)
                router = Router(routing_path, model_overrides=resolved_models)
            else:
                router = Router(routing_path)
        else:
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

        research: ResearchResult | None = None
        if needs_live_research:
            try:
                research = OpenRouterResearcher(
                    self.fcc_client,
                    max_results=research_max_results,
                ).research(goal, research_model or "")
            except (ResearchError, RuntimeError, TypeError, ValueError) as exc:
                return self._blocked_result(
                    plan,
                    started,
                    f"Live web research failed safely: {exc}. No information was fabricated and no files were changed.",
                )
            if not needs_code_changes:
                return TaskResult(
                    TaskStatus.DONE,
                    plan,
                    time.monotonic() - started,
                    research=research,
                )

        try:
            with IsolatedWorkspace(self.repo_root) as workspace:
                try:
                    workspace_context_manager = ContextManager(workspace.path)
                    context = workspace_context_manager.build_context(
                        plan.contract,
                        context_files,
                    )
                    if research is not None:
                        context = _add_research_context(context, research)
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
                except (RuntimeError, TypeError, ValueError) as exc:
                    return self._blocked_result(
                        plan,
                        started,
                        f"Worker pipeline failed safely: {exc}",
                        research=research,
                    )
                if not resolution.success:
                    return TaskResult(
                        TaskStatus.BLOCKED,
                        plan,
                        time.monotonic() - started,
                        resolution=resolution,
                        blocker=resolution.blocker,
                        research=research,
                    )
                try:
                    promoted = workspace.promote()
                except (OSError, RuntimeError, TypeError, ValueError, WorkspaceError) as exc:
                    return self._blocked_result(
                        plan,
                        started,
                        f"Safe promotion failed: {exc}",
                        research=research,
                    )
                worker = replace(
                    resolution.worker,
                    diff=promoted.diff,
                    changed_files=promoted.changed_files,
                    applied=True,
                )
                resolution = replace(resolution, worker=worker)
        except (OSError, WorkspaceError) as exc:
            return self._blocked_result(
                plan,
                started,
                f"Safe workspace failed: {exc}",
                research=research,
            )
        return TaskResult(
            TaskStatus.DONE,
            plan,
            time.monotonic() - started,
            resolution=resolution,
            research=research,
        )

    @staticmethod
    def _blocked_result(
        plan: TaskPlan,
        started: float,
        blocker: str,
        *,
        research: ResearchResult | None = None,
    ) -> TaskResult:
        blocked = ResolverResult(
            ResolutionStatus.BLOCKED,
            0,
            plan.routing,
            blocker=blocker,
        )
        return TaskResult(
            TaskStatus.BLOCKED,
            plan,
            time.monotonic() - started,
            resolution=blocked,
            blocker=blocker,
            research=research,
        )


def _infer_plan_options(goal: str) -> dict[str, object]:
    normalized = goal.lower()
    explicit_multi_agent = any(
        term in normalized
        for term in ("parallel agents", "multi-agent", "multi agent", "consensus", "agent debate")
    )
    uncertainty_signal = explicit_multi_agent or any(
        term in normalized
        for term in (
            "not sure",
            "uncertain",
            "compare approaches",
            "compare options",
            "explore options",
            "choose an approach",
            "unknown cause",
            "rethink architecture",
            "redesign architecture",
        )
    )
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
        "agent debate",
    )
    risk = Risk.HIGH if any(term in normalized for term in high_risk_terms) else Risk.LOW
    if _requires_live_web_research(goal):
        task_type = "research-and-implementation" if _requires_code_changes(goal) else "research"
    elif any(term in normalized for term in ("architecture", "security", "migration")):
        task_type = "architecture" if "architecture" in normalized else "security"
    elif any(
        term in normalized
        for term in (
            "document",
            "readme",
            "docs",
            "write a description",
            "write the description",
            "describe the folder",
            "description of the folder",
        )
    ):
        task_type = "docs"
    elif any(term in normalized for term in ("bug", "fix", "error", "failure")):
        task_type = "bugfix"
    else:
        task_type = "implementation"
    expected_scope = "large" if any(term in normalized for term in ("entire repo", "whole repo", "large refactor")) else "small"
    critical_task = task_type in {"architecture", "security"}
    complexity = 8 if expected_scope == "large" or critical_task or explicit_multi_agent else 4
    high_uncertainty = uncertainty_signal and complexity >= 7
    return {
        "risk": risk,
        "ambiguity": Ambiguity.HIGH if high_uncertainty else Ambiguity.LOW,
        "task_type": task_type,
        "expected_scope": expected_scope,
        "complexity": complexity,
        "uncertainty": 8 if high_uncertainty else 0,
    }


def _requires_live_web_research(goal: str) -> bool:
    normalized = goal.lower()
    return any(
        term in normalized
        for term in (
            "search the web",
            "research the web",
            "search online",
            "research online",
            "web research",
            "browse the web",
            "look online",
            "find businesses",
            "find shops",
            "find restaurants",
            "find contact details",
            "find their email",
            "find their whatsapp",
        )
    )


def _requires_code_changes(goal: str) -> bool:
    normalized = goal.lower()
    return any(
        term in normalized
        for term in (
            "build a website",
            "build the website",
            "create a website",
            "create website",
            "create websites",
            "build an app",
            "create an app",
            "implement",
            "write code",
            "change the code",
            "update the code",
            "add a feature",
            "fix the code",
        )
    )


def _add_research_context(
    context: ContextBundle,
    research: ResearchResult,
) -> ContextBundle:
    source_lines = "\n".join(
        f"- {source.title}: {source.url}" for source in research.sources
    )
    item = ContextItem(
        name="live-web-research",
        content=f"{research.report}\n\nCITED SOURCE URLS:\n{source_lines}",
        priority=99,
        kind="research",
    )
    return ContextBundle(
        items=[*context.items, item],
        max_tokens=context.max_tokens,
    ).trim()
