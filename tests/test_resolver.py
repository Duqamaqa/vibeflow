from collections import deque
import hashlib
from pathlib import Path
import tempfile
import unittest

from vibeflow.budget import BudgetLedger, BudgetPolicy
from vibeflow.changes import ChangeProposal, FileOperation, StructuredChangeApplier
from vibeflow.context import ContextBundle, ContextItem
from vibeflow.contracts import Contract
from vibeflow.resolver import ResolutionStatus, Resolver
from vibeflow.reviewer import ReviewResult
from vibeflow.router import Router, Tier
from vibeflow.worker import WorkerResult
from vibeflow.verifier import CheckStatus, Verifier


class FakeWorker:
    def __init__(self, results):
        self.results = deque(results)
        self.models = []

    def execute(self, contract, model, context, *, feedback=()):
        self.models.append(model)
        return self.results.popleft()


class FakeVerifier:
    def verify(self):
        return {"tests": (True, "ok")}

    def is_green(self, report):
        return True


class ReviewerFactory:
    def __init__(self, results):
        self.results = deque(results)
        self.created = 0

    def __call__(self):
        self.created += 1
        result = self.results.popleft()

        class FakeReviewer:
            def review(self, *args):
                return result

        return FakeReviewer()


class TestResolver(unittest.TestCase):
    def setUp(self):
        self.router = Router("/missing/routing.toml")
        self.contract = Contract("Implement fix", acceptance_criteria=["tests pass"])
        self.context = ContextBundle([ContextItem("task", "contract", 100, "contract")])
        self.base = self.router.route(
            "format", 1, "low", "small", verification_criticality=0
        )

    def test_reviewer_rejection_escalates_with_fresh_reviewer(self):
        workers = FakeWorker([
            WorkerResult(True, "first", "diff", applied=True),
            WorkerResult(True, "second", "diff", applied=True),
        ])
        reviewers = ReviewerFactory([
            ReviewResult(False, "needs fix", ("fix x",), 0.7),
            ReviewResult(True, "approved", (), 0.9),
        ])
        resolver = Resolver(
            worker=workers,
            reviewer_factory=reviewers,
            verifier=FakeVerifier(),
            router=self.router,
            max_iterations=3,
        )
        result = resolver.resolve(self.contract, self.base, self.context)
        self.assertEqual(result.status, ResolutionStatus.DONE)
        self.assertEqual(result.tier_history, ("cheap", "standard"))
        self.assertEqual(reviewers.created, 2)

    def test_uncertainty_escalates_cheap_to_strong_with_cap(self):
        workers = FakeWorker([
            WorkerResult(True, "uncertain", uncertainty=0.9),
            WorkerResult(True, "still uncertain", uncertainty=0.9),
            WorkerResult(True, "certain", "diff", applied=True),
        ])
        reviewers = ReviewerFactory([ReviewResult(True, "approved", (), 1.0)])
        resolver = Resolver(
            worker=workers,
            reviewer_factory=reviewers,
            verifier=FakeVerifier(),
            router=self.router,
            max_iterations=3,
        )
        result = resolver.resolve(self.contract, self.base, self.context)
        self.assertTrue(result.success)
        self.assertEqual(result.tier_history, ("cheap", "standard", "strong"))
        self.assertEqual(result.decision.tier, Tier.STRONG)

    def test_unapplied_proposal_never_reports_done(self):
        resolver = Resolver(
            worker=FakeWorker([WorkerResult(True, "proposal", "diff", applied=False)]),
            reviewer_factory=ReviewerFactory([ReviewResult(True, "approved", (), 1.0)]),
            verifier=FakeVerifier(),
            router=self.router,
            max_iterations=1,
        )
        result = resolver.resolve(self.contract, self.base, self.context)
        self.assertEqual(result.status, ResolutionStatus.BLOCKED)
        self.assertIn("not applied", result.blocker)

    def test_request_budget_stops_before_reviewer_call(self):
        reviewers = ReviewerFactory([ReviewResult(True, "approved", (), 1.0)])
        resolver = Resolver(
            worker=FakeWorker([WorkerResult(True, "proposal", "diff", applied=True)]),
            reviewer_factory=reviewers,
            verifier=FakeVerifier(),
            router=self.router,
            max_iterations=1,
            budget_ledger=BudgetLedger(BudgetPolicy(max_requests=1)),
        )
        result = resolver.resolve(self.contract, self.base, self.context)
        self.assertFalse(result.success)
        self.assertIn("request budget", result.blocker)
        self.assertEqual(reviewers.created, 0)

    def test_failed_review_rolls_back_applied_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "app.py"
            path.write_text("old\n", encoding="utf-8")
            proposal = ChangeProposal((FileOperation(
                "update",
                "app.py",
                content="broken\n",
                expected_sha256=hashlib.sha256(b"old\n").hexdigest(),
            ),))
            resolver = Resolver(
                worker=FakeWorker([WorkerResult(True, "proposal", proposal=proposal)]),
                reviewer_factory=ReviewerFactory([ReviewResult(False, "broken", ("fix it",), 1.0)]),
                verifier=FakeVerifier(),
                router=self.router,
                max_iterations=1,
                change_applier=StructuredChangeApplier(root),
            )

            result = resolver.resolve(self.contract, self.base, self.context)

            self.assertFalse(result.success)
            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")

    def test_docs_only_change_can_pass_without_repository_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proposal = ChangeProposal((FileOperation(
                "create",
                "README.md",
                content="# Project\n",
            ),))
            contract = Contract(
                "Describe the folder",
                acceptance_criteria=["README explains the folder"],
                task_type="docs",
            )
            resolver = Resolver(
                worker=FakeWorker([WorkerResult(True, "docs", proposal=proposal)]),
                reviewer_factory=ReviewerFactory([ReviewResult(True, "approved", (), 1.0)]),
                verifier=Verifier(root),
                router=self.router,
                max_iterations=1,
                change_applier=StructuredChangeApplier(root),
            )

            result = resolver.resolve(contract, self.base, self.context)

            self.assertTrue(result.success)
            self.assertEqual(
                result.verification.by_category("tests")[0].status,
                CheckStatus.SKIPPED,
            )

    def test_docs_task_that_changes_code_still_requires_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proposal = ChangeProposal((FileOperation(
                "create",
                "app.py",
                content="print('hello')\n",
            ),))
            contract = Contract(
                "Document and adjust the app",
                acceptance_criteria=["change is verified"],
                task_type="docs",
            )
            resolver = Resolver(
                worker=FakeWorker([WorkerResult(True, "code", proposal=proposal)]),
                reviewer_factory=ReviewerFactory([ReviewResult(True, "approved", (), 1.0)]),
                verifier=Verifier(root),
                router=self.router,
                max_iterations=1,
                change_applier=StructuredChangeApplier(root),
            )

            result = resolver.resolve(contract, self.base, self.context)

            self.assertFalse(result.success)
            self.assertIn(
                "required tests check was not discovered",
                result.verification.decision.reasons,
            )

    def test_resolver_patch_reapplies_verifies_and_rereviews(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "app.py"
            path.write_text("old\n", encoding="utf-8")
            first = ChangeProposal((FileOperation(
                "update",
                "app.py",
                content="first\n",
                expected_sha256=hashlib.sha256(b"old\n").hexdigest(),
            ),))
            second = ChangeProposal((FileOperation(
                "update",
                "app.py",
                content="fixed\n",
                expected_sha256=hashlib.sha256(b"first\n").hexdigest(),
            ),))
            resolver = Resolver(
                worker=FakeWorker([
                    WorkerResult(True, "first", proposal=first),
                    WorkerResult(True, "second", proposal=second),
                ]),
                reviewer_factory=ReviewerFactory([
                    ReviewResult(False, "needs fix", ("fix it",), 1.0),
                    ReviewResult(True, "approved", (), 1.0),
                ]),
                verifier=FakeVerifier(),
                router=self.router,
                max_iterations=2,
                change_applier=StructuredChangeApplier(root),
            )

            result = resolver.resolve(self.contract, self.base, self.context)

            self.assertTrue(result.success)
            self.assertEqual(path.read_text(encoding="utf-8"), "fixed\n")
            self.assertIn("+fixed", result.worker.diff)


if __name__ == "__main__":
    unittest.main()
