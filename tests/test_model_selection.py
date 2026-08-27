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
model = "nvidia_nim/deepseek-ai/deepseek-v4-flash-0731"
[tiers.standard]
model = "open_router/deepseek/deepseek-v4-pro-0813"
[tiers.strong]
model = "auto:openai-codex"
[candidates]
cheap = []
standard = ["open_router/z-ai/glm-5.2"]
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
                    {"id": "nvidia_nim/deepseek-ai/deepseek-v4-flash-0731"},
                    {"id": "open_router/deepseek/deepseek-v4-pro-0813"},
                    {"id": "openai/gpt-5.2-codex"},
                    {"id": "openai/gpt-5.3-codex"},
                ]
            },
        )

        self.assertEqual(resolved["strong"], "openai/gpt-5.3-codex")

    def test_uses_openrouter_alternative_when_standard_preference_is_absent(self):
        resolved = resolve_tier_models(
            self.config,
            {
                "models": [
                    "nvidia_nim/deepseek-ai/deepseek-v4-flash-0731",
                    "open_router/z-ai/glm-5.2",
                    "openai/gpt-5.3-codex",
                ]
            },
        )

        self.assertEqual(
            resolved["standard"],
            "open_router/z-ai/glm-5.2",
        )

    def test_resolves_fcc_transport_aliases_without_selecting_claude(self):
        resolved = resolve_tier_models(
            self.config,
            {
                "data": [
                    {"id": "claude-3-freecc-no-thinking/nvidia_nim/deepseek-ai/deepseek-v4-flash-0731"},
                    {"id": "anthropic/nvidia_nim/deepseek-ai/deepseek-v4-flash-0731"},
                    {"id": "claude-3-freecc-no-thinking/open_router/deepseek/deepseek-v4-pro-0813"},
                    {"id": "anthropic/open_router/deepseek/deepseek-v4-pro-0813"},
                    {"id": "claude-3-freecc-no-thinking/openai/gpt-5.6-sol"},
                    {"id": "anthropic/open_router/openai/gpt-5.6-sol"},
                    {"id": "anthropic/openai/gpt-5.6-sol"},
                ]
            },
        )

        self.assertEqual(
            resolved["cheap"],
            "anthropic/nvidia_nim/deepseek-ai/deepseek-v4-flash-0731",
        )
        self.assertEqual(
            resolved["standard"],
            "anthropic/open_router/deepseek/deepseek-v4-pro-0813",
        )
        self.assertEqual(resolved["strong"], "anthropic/openai/gpt-5.6-sol")
        self.assertTrue(all("claude" not in model for model in resolved.values()))

    def test_claude_alias_cannot_satisfy_strong_tier(self):
        with self.assertRaises(ModelSelectionError):
            resolve_tier_models(
                self.config,
                {
                    "data": [
                        {"id": "nvidia_nim/deepseek-ai/deepseek-v4-flash-0731"},
                        {"id": "open_router/deepseek/deepseek-v4-pro-0813"},
                        {"id": "claude-3-freecc-no-thinking/openai/gpt-5.6-sol"},
                    ]
                },
            )

    def test_missing_strong_model_fails_closed(self):
        with self.assertRaises(ModelSelectionError):
            resolve_tier_models(
                self.config,
                {"data": [
                    {"id": "nvidia_nim/deepseek-ai/deepseek-v4-flash-0731"},
                    {"id": "open_router/deepseek/deepseek-v4-pro-0813"},
                ]},
            )


if __name__ == "__main__":
    unittest.main()
