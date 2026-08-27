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


if __name__ == "__main__":
    unittest.main()
