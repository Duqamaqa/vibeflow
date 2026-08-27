import tempfile
from pathlib import Path
import unittest

from vibeflow.agent import AgentResult
from vibeflow.context import ContextManager
from vibeflow.orchestrator import Orchestrator, TaskStatus
from vibeflow.resolver import ResolutionStatus, ResolverResult
from vibeflow.reviewer import ReviewResult
from vibeflow.worker import WorkerResult


class TestOrchestrator(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / ".ai").mkdir()
        (self.root / ".ai" / "routing.toml").write_text(
            "[tiers.cheap]\nmodel='cheap'\n[tiers.standard]\nmodel='standard'\n[tiers.strong]\nmodel='strong'\n",
            encoding="utf-8",
        )
        self.resolver_calls = 0

    def tearDown(self):
        self.temp_dir.cleanup()

    def _factory(self, plan):
        outer = self

        class FakeResolver:
            def resolve(self, contract, routing, context):
                outer.resolver_calls += 1
                return ResolverResult(
                    ResolutionStatus.DONE,
                    1,
                    routing,
                    WorkerResult(True, "done", "diff", applied=True),
                    ReviewResult(True, "approved", (), 1.0),
                    {"tests": (True, "ok")},
                    (routing.tier.value,),
                )

        return FakeResolver()

    def _orchestrator(self, strategy_executor_factory=None):
        return Orchestrator(
            fcc_client=object(),
            context_manager=ContextManager(self.root),
            telemetry_dir=self.root / ".vibeflow",
            resolver_factory=self._factory,
            strategy_executor_factory=strategy_executor_factory,
        )

    def test_low_risk_clear_task_runs_end_to_end(self):
        result = self._orchestrator().execute_task(
            "Implement feature", acceptance_criteria=["tests pass"]
        )
        self.assertEqual(result.status, TaskStatus.DONE)
        self.assertEqual(self.resolver_calls, 1)

    def test_material_ambiguity_stops_for_approval(self):
        result = self._orchestrator().execute_task(
            "Migrate storage",
            acceptance_criteria=["data preserved"],
            ambiguity="high",
        )
        self.assertEqual(result.status, TaskStatus.NEEDS_APPROVAL)
        self.assertEqual(self.resolver_calls, 0)
        self.assertTrue(result.plan.contract.reverse_questions())

    def test_dry_run_is_side_effect_free(self):
        result = self._orchestrator().execute_task("Plan change", dry_run=True)
        self.assertEqual(result.status, TaskStatus.PLANNED)
        self.assertEqual(self.resolver_calls, 0)

    def test_high_uncertainty_consensus_is_wired_into_context(self):
        class FakeExecutor:
            def __init__(self):
                self.calls = 0

            def execute(self, request):
                self.calls += 1
                return AgentResult("use option A", decision="option A")

        executor = FakeExecutor()
        result = self._orchestrator(lambda model: executor).execute_task(
            "Choose architecture",
            acceptance_criteria=["decision is justified"],
            complexity=8,
            uncertainty=9,
            expected_scope="large",
        )
        self.assertEqual(result.status, TaskStatus.DONE)
        self.assertEqual(executor.calls, 3)
        self.assertIn("consensus-synthesis", [item.name for item in result.plan.context.items])

    def test_execution_failure_returns_blocked_result(self):
        def failing_factory(plan):
            class FailingResolver:
                def resolve(self, contract, routing, context):
                    raise RuntimeError("gateway unavailable")

            return FailingResolver()

        orchestrator = Orchestrator(
            fcc_client=object(),
            context_manager=ContextManager(self.root),
            telemetry_dir=self.root / ".vibeflow",
            resolver_factory=failing_factory,
        )

        result = orchestrator.execute_task("Implement safely")

        self.assertEqual(result.status, TaskStatus.BLOCKED)
        self.assertIn("RuntimeError", result.blocker)


if __name__ == "__main__":
    unittest.main()
