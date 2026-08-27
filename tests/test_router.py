import tempfile
from pathlib import Path
import unittest

from vibeflow.router import Router, Tier


class TestRouter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = Path(self.temp_dir.name) / "routing.toml"
        self.config.write_text(
            """
[tiers.cheap]
model = "test/cheap"
[tiers.standard]
model = "test/standard"
[tiers.strong]
model = "test/strong"
[policy]
max_escalations = 2
""",
            encoding="utf-8",
        )
        self.router = Router(self.config)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_nested_toml_is_loaded(self):
        self.assertEqual(self.router.model_for(Tier.CHEAP), "test/cheap")
        self.assertEqual(self.router.model_for(Tier.STRONG), "test/strong")

    def test_cost_tiers_are_semantic(self):
        cheap = self.router.route("format", 1, "low", "small", verification_criticality=0)
        standard = self.router.route("implementation", 5, "low", "small", verification_criticality=1)
        strong = self.router.route("security migration", 8, "high", "large")
        self.assertEqual(cheap.tier, Tier.CHEAP)
        self.assertEqual(standard.tier, Tier.STANDARD)
        self.assertEqual(strong.tier, Tier.STRONG)
        self.assertTrue(cheap.review_required)

    def test_previous_failures_escalate_and_cap(self):
        base = self.router.route("format", 1, "low", "small", verification_criticality=0)
        once = self.router.escalate(base, "failed")
        twice = self.router.escalate(once, "failed again")
        capped = self.router.escalate(twice, "third failure")
        self.assertEqual(once.tier, Tier.STANDARD)
        self.assertEqual(twice.tier, Tier.STRONG)
        self.assertEqual(capped.tier, Tier.STRONG)
        self.assertEqual(capped.escalation_count, 2)

    def test_high_uncertainty_uses_bounded_multi_agent_strategy(self):
        decision = self.router.route(
            "architecture",
            8,
            "high",
            "large",
            uncertainty=9,
            verification_criticality=9,
        )
        self.assertEqual(decision.strategy, "debate")

    def test_cto_override_is_explicit(self):
        decision = self.router.route("format", 1, "low", "small", cto_override="strong")
        self.assertEqual(decision.tier, Tier.STRONG)
        self.assertIn("Layer-1 CTO override", decision.reasons)


if __name__ == "__main__":
    unittest.main()
