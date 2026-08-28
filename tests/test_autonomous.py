import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.vibeflow.autonomous import (
    AutonomousRunner,
    _infer_plan_options,
    _requires_code_changes,
    _requires_live_web_research,
)
from src.vibeflow.changes import ApplyResult
from src.vibeflow.fcc_client import FCCTransportError
from src.vibeflow.resolver import ResolutionStatus, ResolverResult
from src.vibeflow.router import Router
from src.vibeflow.skills import RepositorySkillStore
from src.vibeflow.worker import WorkerResult


class FakeFCC:
    def list_models(self):
        return {"data": []}


class ResearchFCC:
    def __init__(self):
        self.requests = []

    def list_models(self):
        return {
            "data": [
                {"id": "open_router/google/gemini-3-flash-preview"},
                {"id": "test/cheap"},
                {"id": "test/standard"},
                {"id": "test/strong"},
            ]
        }

    def create_response(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(
            text="Verified finding from [Official source](https://example.com/source).",
            raw=[],
            usage={"input_tokens": 10, "output_tokens": 12},
            request_id="research-request",
        )


class FakeWorkspace:
    def __init__(self, repo_root):
        self.path = Path(repo_root)
        self.applier = object()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def promote(self):
        return ApplyResult(True, (), "")


class CapturingResolver:
    context = None

    def __init__(self, *args, **kwargs):
        pass

    def resolve(self, contract, routing, context):
        type(self).context = context
        return ResolverResult(
            ResolutionStatus.DONE,
            1,
            routing,
            worker=WorkerResult(True, "No changes needed"),
        )


class ExplodingResolver:
    def __init__(self, *args, **kwargs):
        pass

    def resolve(self, contract, routing, context):
        raise TypeError("malformed worker metadata")


class PipelineFCC:
    def __init__(
        self,
        target: Path,
        *,
        review_approved: bool = True,
        fail_cheap_worker: bool = False,
    ):
        self.target = target
        self.review_approved = review_approved
        self.fail_cheap_worker = fail_cheap_worker
        self.prompts = []
        self.target_values_during_review = []

    def list_models(self):
        return {
            "data": [
                {"id": "test/cheap"},
                {"id": "test/standard"},
                {"id": "test/strong"},
            ]
        }

    def create_response(self, *, model, input, stream):
        self.prompts.append((model, input))
        if input.startswith("You are a fresh independent reviewer"):
            self.target_values_during_review.append(
                (self.target / "app.py").read_text(encoding="utf-8")
            )
            return SimpleNamespace(
                text=json.dumps({
                    "approved": self.review_approved,
                    "feedback": "approved" if self.review_approved else "reject this change",
                    "required_changes": [] if self.review_approved else ["keep the original value"],
                    "confidence": 1.0,
                }),
                usage={},
                request_id="review-request",
            )
        if self.fail_cheap_worker and model == "test/cheap":
            raise FCCTransportError("cheap provider unavailable")
        expected = hashlib.sha256(b"VALUE = 1\n").hexdigest()
        return SimpleNamespace(
            text=json.dumps({
                "success": True,
                "summary": "PRIVATE_WORKER_NOTE",
                "operations": [{
                    "op": "update",
                    "path": "app.py",
                    "content": "VALUE = 2\n",
                    "expected_sha256": expected,
                }],
                "uncertainty": 0.0,
            }),
            usage={},
            request_id="worker-request",
        )


class TestAutonomousSkills(unittest.TestCase):
    def test_live_web_research_is_detected_before_model_execution(self):
        self.assertTrue(
            _requires_live_web_research(
                "Search the web and find restaurants without websites"
            )
        )
        options = _infer_plan_options("Search the web and find restaurants")
        self.assertEqual(options["task_type"], "research")

    def test_live_web_research_returns_cited_report_without_file_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".ai").mkdir()
            (root / ".ai" / "routing.toml").write_text(
                """[tiers.cheap]
model = "provider/cheap"
[tiers.standard]
model = "provider/standard"
[tiers.strong]
model = "provider/strong"
""",
                encoding="utf-8",
            )

            fcc = ResearchFCC()
            result = AutonomousRunner(root, fcc_client=fcc).run(
                "Search the web and find restaurants without websites"
            )

            self.assertTrue(result.success)
            self.assertEqual(result.plan.contract.task_type, "research")
            self.assertEqual(result.research.sources[0].url, "https://example.com/source")
            self.assertTrue(fcc.requests[0]["model"].endswith(":online"))
            self.assertEqual(fcc.requests[0]["plugins"][0]["id"], "web")
            self.assertIsNone(result.resolution)
            self.assertLess(result.duration_seconds, 1.0)

    def test_research_and_implementation_adds_cited_context_before_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".ai").mkdir()
            (root / ".ai" / "routing.toml").write_text(
                """[tiers.cheap]
model = "test/cheap"
[tiers.standard]
model = "test/standard"
[tiers.strong]
model = "test/strong"
[research]
model = "open_router/google/gemini-3-flash-preview"
""",
                encoding="utf-8",
            )
            with (
                patch("src.vibeflow.autonomous.IsolatedWorkspace", FakeWorkspace),
                patch("src.vibeflow.autonomous.Resolver", CapturingResolver),
            ):
                result = AutonomousRunner(root, fcc_client=ResearchFCC()).run(
                    "Search the web for a restaurant and create a website"
                )

            self.assertTrue(result.success)
            self.assertEqual(result.plan.contract.task_type, "research-and-implementation")
            research_items = [
                item for item in CapturingResolver.context.items if item.kind == "research"
            ]
            self.assertEqual(len(research_items), 1)
            self.assertIn("https://example.com/source", research_items[0].content)

    def test_code_change_intent_is_separate_from_research_intent(self):
        self.assertFalse(_requires_code_changes("Search the web for restaurants"))
        self.assertTrue(_requires_code_changes("Search the web and create a website"))
    def test_folder_description_is_classified_as_documentation(self):
        options = _infer_plan_options("Write a description of the folder for visitors")

        self.assertEqual(options["task_type"], "docs")

    def test_worker_exception_is_not_reported_as_workspace_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".ai").mkdir()
            (root / ".ai" / "routing.toml").write_text(
                """[tiers.cheap]
model = "provider/cheap"
[tiers.standard]
model = "provider/standard"
[tiers.strong]
model = "provider/strong"
""",
                encoding="utf-8",
            )
            models = {
                "cheap": "provider/cheap",
                "standard": "provider/standard",
                "strong": "provider/strong",
            }

            with (
                patch("src.vibeflow.autonomous.resolve_tier_models", return_value=models),
                patch("src.vibeflow.autonomous.IsolatedWorkspace", FakeWorkspace),
                patch("src.vibeflow.autonomous.Resolver", ExplodingResolver),
            ):
                result = AutonomousRunner(root, fcc_client=FakeFCC()).run("Create a file")

            self.assertFalse(result.success)
            self.assertIn("Worker pipeline failed safely", result.blocker)
            self.assertNotIn("workspace", result.blocker.lower())

    def test_multi_agent_strategy_is_inferred_from_natural_language(self):
        options = _infer_plan_options(
            "Compare approaches and rethink architecture with parallel agents"
        )
        routing = Router("missing.toml").route(
            task_type=options["task_type"],
            complexity=options["complexity"],
            risk=options["risk"].value,
            expected_scope=options["expected_scope"],
            uncertainty=options["uncertainty"],
        )

        self.assertEqual(options["ambiguity"].value, "high")
        self.assertEqual(routing.strategy, "consensus")

    def test_agent_debate_request_is_high_risk_and_requires_debate(self):
        options = _infer_plan_options("Use agent debate for this security architecture")
        routing = Router("missing.toml").route(
            task_type=options["task_type"],
            complexity=options["complexity"],
            risk=options["risk"].value,
            expected_scope=options["expected_scope"],
            uncertainty=options["uncertainty"],
        )

        self.assertEqual(options["risk"].value, "high")
        self.assertEqual(routing.strategy, "debate")

    def test_selected_skill_reaches_autonomous_resolver_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".ai").mkdir()
            (root / ".ai" / "routing.toml").write_text(
                """[tiers.cheap]
model = "provider/cheap"
[tiers.standard]
model = "provider/standard"
[tiers.strong]
model = "provider/strong"
""",
                encoding="utf-8",
            )
            RepositorySkillStore(root).create(
                name="plain-docs",
                description="Write documentation for beginners",
                instructions="Use plain language and define every technical term.",
            )
            models = {
                "cheap": "provider/cheap",
                "standard": "provider/standard",
                "strong": "provider/strong",
            }

            with (
                patch("src.vibeflow.autonomous.resolve_tier_models", return_value=models),
                patch("src.vibeflow.autonomous.IsolatedWorkspace", FakeWorkspace),
                patch("src.vibeflow.autonomous.Resolver", CapturingResolver),
            ):
                result = AutonomousRunner(root, fcc_client=FakeFCC()).run(
                    "Improve the README",
                    selected_skills=("plain-docs",),
                )

            self.assertTrue(result.success)
            skill_items = [
                item
                for item in CapturingResolver.context.items
                if item.kind == "skill"
            ]
            self.assertEqual([item.name for item in skill_items], ["plain-docs"])
            self.assertIn("plain language", skill_items[0].content)


class TestAutonomousPipeline(unittest.TestCase):
    def _repository(self, root: Path) -> str:
        (root / ".ai").mkdir()
        (root / "tests").mkdir()
        (root / ".ai" / "routing.toml").write_text(
            """[tiers.cheap]
model = "test/cheap"
[tiers.standard]
model = "test/standard"
[tiers.strong]
model = "test/strong"
""",
            encoding="utf-8",
        )
        (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "tests" / "test_app.py").write_text(
            """import unittest
from app import VALUE

class TestApp(unittest.TestCase):
    def test_value(self):
        self.assertEqual(VALUE, 2)
""",
            encoding="utf-8",
        )
        subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(
            [
                "git", "-C", str(root),
                "-c", "user.name=Vibeflow Test",
                "-c", "user.email=test@example.invalid",
                "commit", "-m", "initial",
            ],
            check=True,
            capture_output=True,
        )
        return subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_full_pipeline_reviews_before_promoting_without_committing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original_head = self._repository(root)
            client = PipelineFCC(root)

            result = AutonomousRunner(root, fcc_client=client, max_iterations=1).run(
                "Update app.py so VALUE is 2",
                context_files=("app.py",),
            )

            self.assertTrue(result.success)
            self.assertEqual((root / "app.py").read_text(encoding="utf-8"), "VALUE = 2\n")
            self.assertTrue(result.resolution.verification.accepted)
            self.assertTrue(result.resolution.review.approved)
            self.assertEqual(client.target_values_during_review, ["VALUE = 1\n"])
            review_prompt = client.prompts[1][1]
            self.assertIn("=== DIFF ===", review_prompt)
            self.assertIn("=== VERIFICATION ===", review_prompt)
            self.assertNotIn("PRIVATE_WORKER_NOTE", review_prompt)
            current_head = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(current_head, original_head)

    def test_reviewer_rejection_never_promotes_to_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            client = PipelineFCC(root, review_approved=False)

            result = AutonomousRunner(root, fcc_client=client, max_iterations=1).run(
                "Update app.py so VALUE is 2",
                context_files=("app.py",),
            )

            self.assertFalse(result.success)
            self.assertEqual((root / "app.py").read_text(encoding="utf-8"), "VALUE = 1\n")
            self.assertFalse(result.resolution.review.approved)
            self.assertEqual(client.target_values_during_review, ["VALUE = 1\n"])

    def test_worker_provider_failure_escalates_to_next_tier(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            client = PipelineFCC(root, fail_cheap_worker=True)

            result = AutonomousRunner(root, fcc_client=client, max_iterations=2).run(
                "Document the update to app.py so VALUE is 2",
                context_files=("app.py",),
            )

            self.assertTrue(result.success)
            self.assertEqual(result.resolution.tier_history, ("cheap", "standard"))
            self.assertIn("provider failed safely", result.resolution.feedback_history[0])


if __name__ == "__main__":
    unittest.main()
