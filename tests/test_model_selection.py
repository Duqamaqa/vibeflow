from pathlib import Path
import tempfile
import unittest

from vibeflow.model_selection import ModelSelectionError, resolve_tier_models


class TestModelSelection(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.config = Path(self.temporary.name) / "routing.toml"
        self.config.write_text(
            """
[tiers.cheap]
model = "openrouter/deepseek/deepseek-v4-flash-0731"
[tiers.standard]
model = "openrouter/deepseek/deepseek-v4-pro-0813"
[tiers.strong]
model = "auto:openai-codex"
[candidates]
cheap = []
standard = ["nvidia_nim/deepseek-ai/deepseek-v4-pro-0813"]
strong = []
""",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_resolves_configured_workers_and_best_live_openai_coder(self):
        resolved = resolve_tier_models(
            self.config,
            {
                "data": [
                    {"id": "openrouter/deepseek/deepseek-v4-flash-0731"},
                    {"id": "openrouter/deepseek/deepseek-v4-pro-0813"},
                    {"id": "openai/gpt-5.2-codex"},
                    {"id": "openai/gpt-5.3-codex"},
                ]
            },
        )

        self.assertEqual(resolved["strong"], "openai/gpt-5.3-codex")

    def test_uses_nvidia_alternative_when_openrouter_standard_is_absent(self):
        resolved = resolve_tier_models(
            self.config,
            {
                "models": [
                    "openrouter/deepseek/deepseek-v4-flash-0731",
                    "nvidia_nim/deepseek-ai/deepseek-v4-pro-0813",
                    "openai/gpt-5.3-codex",
                ]
            },
        )

        self.assertEqual(
            resolved["standard"],
            "nvidia_nim/deepseek-ai/deepseek-v4-pro-0813",
        )

    def test_missing_strong_model_fails_closed(self):
        with self.assertRaises(ModelSelectionError):
            resolve_tier_models(
                self.config,
                {"data": [
                    {"id": "openrouter/deepseek/deepseek-v4-flash-0731"},
                    {"id": "openrouter/deepseek/deepseek-v4-pro-0813"},
                ]},
            )


if __name__ == "__main__":
    unittest.main()
