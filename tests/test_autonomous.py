import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.vibeflow.autonomous import AutonomousRunner
from src.vibeflow.changes import ApplyResult
from src.vibeflow.resolver import ResolutionStatus, ResolverResult
from src.vibeflow.skills import RepositorySkillStore
from src.vibeflow.worker import WorkerResult


class FakeFCC:
    def list_models(self):
        return {"data": []}


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


class TestAutonomousSkills(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
