import unittest
from pathlib import Path
import tempfile

from src.vibeflow.skills import (
    Skill,
    SkillCost,
    SkillMetadata,
    SkillRegistry,
    SkillRisk,
    RepositorySkillStore,
    load_repository_skills,
)
from src.vibeflow.safety import SafetyViolation


class TestSkillRegistry(unittest.TestCase):
    def test_prompt_loading_is_lazy_and_cached(self):
        calls = []

        def loader():
            calls.append("load")
            return "Reusable instructions"

        skill = Skill(
            SkillMetadata(
                name="review",
                description="Review code",
                triggers=("review code",),
                capabilities=frozenset({"review"}),
            ),
            loader,
        )
        registry = SkillRegistry()
        registry.register(skill)

        self.assertFalse(skill.prompt_loaded)
        self.assertIs(registry.select("Please review code"), skill)
        self.assertEqual(calls, [])
        self.assertEqual(registry.load_prompt("REVIEW"), "Reusable instructions")
        self.assertEqual(registry.load_prompt("review"), "Reusable instructions")
        self.assertEqual(calls, ["load"])

    def test_selection_filters_capability_cost_and_risk(self):
        registry = SkillRegistry()
        safe = Skill(
            SkillMetadata(
                name="safe-deploy",
                description="Prepare safe deployments",
                triggers=("deploy",),
                capabilities=frozenset({"deployment"}),
                cost="low",
                risk="low",
            ),
            lambda: "safe",
        )
        risky = Skill(
            SkillMetadata(
                name="direct-deploy",
                description="Deploy directly",
                triggers=("deploy", "deploy production"),
                capabilities=frozenset({"deployment"}),
                cost=SkillCost.MEDIUM,
                risk=SkillRisk.HIGH,
            ),
            lambda: "risky",
        )
        registry.register(risky)
        registry.register(safe)

        matches = registry.find(
            "Deploy production",
            required_capabilities=("deployment",),
            max_cost=SkillCost.MEDIUM,
            max_risk=SkillRisk.MEDIUM,
        )

        self.assertEqual(
            [match.skill.metadata.name for match in matches],
            ["safe-deploy"],
        )

    def test_matching_and_tie_breaks_are_deterministic(self):
        registry = SkillRegistry()
        for name in ("zeta", "alpha"):
            registry.register(
                Skill(
                    SkillMetadata(
                        name=name,
                        description=name,
                        triggers=("analyze",),
                    ),
                    lambda: "prompt",
                )
            )

        self.assertEqual(
            [match.skill.metadata.name for match in registry.find("Analyze this")],
            ["alpha", "zeta"],
        )

    def test_trigger_matching_uses_word_boundaries(self):
        registry = SkillRegistry()
        registry.register(
            Skill(
                SkillMetadata(
                    name="git",
                    description="Git operations",
                    triggers=("git",),
                ),
                lambda: "prompt",
            )
        )

        self.assertEqual(registry.find("digital design"), ())
        self.assertEqual(len(registry.find("inspect git history")), 1)

    def test_duplicate_names_are_case_insensitive(self):
        registry = SkillRegistry()
        registry.register(
            Skill(SkillMetadata("Review", "One", ("review",)), lambda: "one")
        )

        with self.assertRaises(ValueError):
            registry.register(
                Skill(SkillMetadata("review", "Two", ("review",)), lambda: "two")
            )


class TestRepositorySkillStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_load_and_remove_instruction_skill(self):
        store = RepositorySkillStore(self.root)
        store.create(
            name="frontend-a11y",
            description="Apply the frontend accessibility standard",
            instructions="Require labels, focus states, and keyboard support.",
            triggers=("accessibility", "frontend"),
            capabilities=("ui",),
        )

        catalog = load_repository_skills(self.root)

        self.assertEqual(catalog.errors, ())
        self.assertEqual(catalog.registry.names(), ("frontend-a11y",))
        self.assertIn("keyboard support", catalog.registry.load_prompt("frontend-a11y"))
        store.remove("frontend-a11y")
        self.assertEqual(store.catalog().registry.names(), ())

    def test_import_reads_only_skill_document(self):
        source = self.root / "external"
        source.mkdir()
        (source / "SKILL.md").write_text(
            """---
name: "review-pro"
description: "Review production changes"
triggers:
  - "code review"
risk: medium
---

Review correctness, security, and regressions.
""",
            encoding="utf-8",
        )
        (source / "run.sh").write_text("echo should-not-copy\n", encoding="utf-8")
        target = self.root / "target"
        target.mkdir()

        metadata = RepositorySkillStore(target).import_from(source)

        self.assertEqual(metadata.name, "review-pro")
        self.assertFalse((target / ".ai" / "skills" / "review-pro" / "run.sh").exists())

    def test_import_rejects_secrets_and_symlinks(self):
        source = self.root / "unsafe"
        source.mkdir()
        token = "sk-proj-" + "abcdefghijklmnopqrstuvwxyz"
        (source / "SKILL.md").write_text(
            f"---\nname: unsafe\ndescription: unsafe\n---\nUse {token}\n",
            encoding="utf-8",
        )
        target = self.root / "target"
        target.mkdir()

        with self.assertRaises(SafetyViolation):
            RepositorySkillStore(target).import_from(source)

        linked = self.root / "linked"
        linked.symlink_to(source, target_is_directory=True)
        with self.assertRaises(SafetyViolation):
            RepositorySkillStore(target).import_from(linked)

    def test_import_rejects_symbolic_linked_document(self):
        source = self.root / "source"
        source.mkdir()
        instructions = self.root / "instructions.md"
        instructions.write_text(
            "---\nname: linked\ndescription: linked\n---\nUnsafe link.\n",
            encoding="utf-8",
        )
        (source / "SKILL.md").symlink_to(instructions)
        target = self.root / "target"
        target.mkdir()

        with self.assertRaises(SafetyViolation):
            RepositorySkillStore(target).import_from(source)

    def test_remove_preserves_skill_when_folder_has_extra_files(self):
        store = RepositorySkillStore(self.root)
        store.create(
            name="docs",
            description="Improve documentation",
            instructions="Write clear documentation.",
        )
        skill_directory = self.root / ".ai" / "skills" / "docs"
        (skill_directory / "notes.txt").write_text("keep", encoding="utf-8")

        with self.assertRaises(SafetyViolation):
            store.remove("docs")

        self.assertTrue((skill_directory / "SKILL.md").is_file())
        self.assertTrue((skill_directory / "notes.txt").is_file())


if __name__ == "__main__":
    unittest.main()
