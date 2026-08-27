from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from vibeflow.context import ContextBundle, ContextItem
from vibeflow.contracts import Contract
from vibeflow.reviewer import Reviewer


class TestReviewer(unittest.TestCase):
    def test_reviewer_gets_evidence_not_implementer_history(self):
        client = MagicMock()
        client.create_response.return_value = SimpleNamespace(
            text='{"approved": true, "feedback": "green", "required_changes": [], "confidence": 0.9}',
            request_id="req-review",
        )
        context = ContextBundle([ContextItem("task", "bounded context", 100, "contract")])
        reviewer = Reviewer(client)
        result = reviewer.review(
            Contract("Ship fix", acceptance_criteria=["tests pass"]),
            context,
            "diff --git a/a b/a",
            {"tests": {"status": "passed"}},
            "test/reviewer",
        )
        self.assertTrue(result.approved)
        prompt = client.create_response.call_args.kwargs["input"]
        self.assertIn("bounded context", prompt)
        self.assertIn("diff --git", prompt)
        self.assertNotIn("private implementer chain of thought", prompt)

    def test_legacy_response_parser_is_safe(self):
        result = Reviewer.parse_review(
            "APPROVED: NO\nFEEDBACK: Fix it\nREQUIRED_CHANGES: ['fix x']"
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.required_changes, ("fix x",))


if __name__ == "__main__":
    unittest.main()
