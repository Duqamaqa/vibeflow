import hashlib
import json
import unittest

from vibeflow.worker import Worker


class TestWorkerStructuredOutput(unittest.TestCase):
    def test_parses_explicit_operations_not_worker_claimed_diff(self):
        payload = {
            "success": True,
            "summary": "updated file",
            "operations": [{
                "op": "update",
                "path": "app.py",
                "content": "new\n",
                "expected_sha256": hashlib.sha256(b"old\n").hexdigest(),
            }],
            "uncertainty": 0.1,
        }

        result = Worker._parse_response(
            json.dumps(payload),
            usage={},
            request_id="request-1",
        )

        self.assertTrue(result.success)
        self.assertIsNotNone(result.proposal)
        self.assertEqual(result.changed_files, ("app.py",))
        self.assertFalse(result.applied)
        self.assertEqual(result.diff, "")

    def test_unstructured_output_fails_closed(self):
        result = Worker._parse_response(
            "diff --git a/app.py b/app.py",
            usage={},
            request_id=None,
        )

        self.assertFalse(result.success)
        self.assertIsNone(result.proposal)
        self.assertEqual(result.diff, "")

    def test_extracts_one_structured_proposal_after_provider_reasoning(self):
        payload = {
            "success": True,
            "summary": "created docs",
            "operations": [{
                "op": "create",
                "path": "README.md",
                "content": "Description\n",
            }],
            "uncertainty": 0.0,
        }

        result = Worker._parse_response(
            f"I will now return the requested object.\n{json.dumps(payload)}",
            usage={},
            request_id="request-wrapped",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.changed_files, ("README.md",))

    def test_empty_uncertainty_list_is_treated_as_no_uncertainty(self):
        payload = {
            "success": True,
            "summary": "created docs",
            "operations": [{
                "op": "create",
                "path": "README.md",
                "content": "Description\n",
            }],
            "uncertainty": [],
        }

        result = Worker._parse_response(
            json.dumps(payload),
            usage={},
            request_id="request-empty-list",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.uncertainty, 0.0)

    def test_list_uncertainty_fails_closed_without_raising(self):
        payload = {
            "success": True,
            "summary": "updated file",
            "operations": [{
                "op": "create",
                "path": "README.md",
                "content": "Description\n",
            }],
            "uncertainty": [0.1],
        }

        result = Worker._parse_response(
            json.dumps(payload),
            usage={},
            request_id="request-list",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.uncertainty, 0.0)
        self.assertIn("uncertainty must be a JSON number", result.summary)


if __name__ == "__main__":
    unittest.main()
