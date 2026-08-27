import unittest

from src.vibeflow.skills import (
    Skill,
    SkillCost,
    SkillMetadata,
    SkillRegistry,
    SkillRisk,
)


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


if __name__ == "__main__":
    unittest.main()

