import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from src.vibeflow.budget import CostEstimator
from src.vibeflow.telemetry import Telemetry, infer_provider


class TestTelemetry(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sqlite_schema_and_complete_event(self):
        estimator = CostEstimator(
            {
                "provider/model": {
                    "input_per_million": "2",
                    "output_per_million": "4",
                }
            }
        )
        telemetry = Telemetry(self.log_dir, cost_estimator=estimator)

        row_id = telemetry.log_event(
            task_id="task-1",
            agent_role="worker",
            model="provider/model",
            tier="standard",
            usage={
                "input_tokens": 10,
                "output_tokens": 5,
                "input_tokens_details": {"cached_tokens": 4},
            },
            duration=2.5,
            request_id="req-1",
            success=True,
            timestamp=1234.5,
            strategy="review",
            escalation_count=1,
            attempt=2,
        )

        self.assertEqual(row_id, 1)
        with closing(sqlite3.connect(telemetry.db_path)) as connection:
            connection.row_factory = sqlite3.Row
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(telemetry)")
            }
            row = connection.execute("SELECT * FROM telemetry").fetchone()

        self.assertTrue(
            {
                "timestamp",
                "task_id",
                "agent_role",
                "provider",
                "model",
                "tier",
                "input_tokens",
                "output_tokens",
                "cache_tokens",
                "total_tokens",
                "duration_ms",
                "request_id",
                "success",
                "error_type",
                "strategy",
                "escalation_count",
                "estimated_cost_usd",
                "metadata_json",
            }.issubset(columns)
        )
        self.assertEqual(row["timestamp"], 1234.5)
        self.assertEqual(row["provider"], "provider")
        self.assertEqual(row["total_tokens"], 15)
        self.assertEqual(row["cache_tokens"], 4)
        self.assertEqual(row["duration_ms"], 2500.0)
        self.assertEqual(row["success"], 1)
        self.assertEqual(row["strategy"], "review")
        self.assertEqual(row["escalation_count"], 1)
        self.assertAlmostEqual(row["estimated_cost_usd"], 0.00004)
        self.assertEqual(json.loads(row["metadata_json"]), {"attempt": 2})

    def test_provider_override_legacy_usage_and_failure(self):
        telemetry = Telemetry(self.log_dir)
        telemetry.log_event(
            task_id="task-2",
            agent_role="reviewer",
            model="model-without-prefix",
            tier="cheap",
            usage={"prompt_tokens": 7, "completion_tokens": 3},
            duration=0.1,
            request_id=None,
            success=False,
            provider="explicit",
            error_type="ValidationError",
        )

        with closing(sqlite3.connect(telemetry.db_path)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM telemetry").fetchone()

        self.assertEqual(row["provider"], "explicit")
        self.assertEqual(row["input_tokens"], 7)
        self.assertEqual(row["output_tokens"], 3)
        self.assertEqual(row["total_tokens"], 10)
        self.assertEqual(row["success"], 0)
        self.assertEqual(row["error_type"], "ValidationError")
        self.assertIsNone(row["estimated_cost_usd"])

    def test_provider_inference_is_explicit_and_conservative(self):
        self.assertEqual(infer_provider("nvidia_nim/nvidia/model"), "nvidia_nim")
        self.assertIsNone(infer_provider("model-without-provider"))
        self.assertIsNone(infer_provider("/missing-provider"))

    def test_report_aggregates_and_filters_without_assuming_prices(self):
        telemetry = Telemetry(self.log_dir)
        telemetry.log_event(
            "task-a",
            "worker",
            "alpha/model-1",
            "standard",
            {"input_tokens": 10, "output_tokens": 2},
            0.5,
            "req-1",
            True,
            estimated_cost_usd=Decimal("0.25"),
        )
        telemetry.log_event(
            "task-a",
            "reviewer",
            "alpha/model-2",
            "strong",
            {"input_tokens": 4, "output_tokens": 1},
            0.25,
            "req-2",
            False,
            error_type="Rejected",
        )
        telemetry.log_event(
            "task-b",
            "worker",
            "local-model",
            "cheap",
            None,
            0.1,
            None,
            True,
            estimated_cost_usd=0,
        )

        report = telemetry.report()

        self.assertEqual(report["totals"]["request_count"], 3)
        self.assertEqual(report["totals"]["successful_requests"], 2)
        self.assertEqual(report["totals"]["failed_requests"], 1)
        self.assertEqual(report["totals"]["input_tokens"], 14)
        self.assertEqual(report["totals"]["total_tokens"], 17)
        self.assertEqual(report["totals"]["priced_requests"], 2)
        self.assertEqual(report["totals"]["unpriced_requests"], 1)
        self.assertAlmostEqual(report["totals"]["estimated_cost_usd"], 0.25)
        self.assertEqual(
            [row["provider"] for row in report["by_provider"]],
            ["alpha", "unknown"],
        )

        task_report = telemetry.report(task_id="task-a")
        self.assertEqual(task_report["totals"]["request_count"], 2)
        self.assertEqual(len(task_report["by_provider"]), 1)
        self.assertEqual(task_report["by_provider"][0]["provider"], "alpha")
        self.assertEqual(
            [row["agent_role"] for row in task_report["by_agent_role"]],
            ["reviewer", "worker"],
        )
        self.assertEqual(
            [row["tier"] for row in report["by_tier"]],
            ["cheap", "standard", "strong"],
        )
        self.assertEqual(
            [row["strategy"] for row in report["by_strategy"]],
            ["unspecified"],
        )


if __name__ == "__main__":
    unittest.main()
