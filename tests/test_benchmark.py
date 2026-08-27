import unittest
from unittest.mock import Mock

from src.vibeflow.benchmark import (
    DEFAULT_BENCHMARK_CASES,
    BenchmarkCase,
    BenchmarkRecord,
    BenchmarkResponse,
    BenchmarkRunner,
    BenchmarkStatus,
    ModelSpec,
    estimate_cost,
    recommend_tier_mappings,
)


class TestBenchmarkCases(unittest.TestCase):
    def test_default_cases_are_representative(self):
        self.assertGreaterEqual(len(DEFAULT_BENCHMARK_CASES), 3)
        self.assertEqual(
            {case.category for case in DEFAULT_BENCHMARK_CASES},
            {"implementation", "testing", "architecture"},
        )
        self.assertEqual(
            len({case.case_id for case in DEFAULT_BENCHMARK_CASES}),
            len(DEFAULT_BENCHMARK_CASES),
        )

    def test_quality_proxy_is_deterministic_term_coverage(self):
        case = BenchmarkCase(
            "case",
            "testing",
            "prompt",
            ("empty", "boundary", "error"),
        )
        self.assertEqual(case.quality_proxy("Empty and ERROR cases"), 2 / 3)
        self.assertEqual(case.quality_proxy("empty boundary error"), 1.0)
        self.assertEqual(case.quality_proxy("unrelated"), 0.0)


class TestBenchmarkRunner(unittest.TestCase):
    def setUp(self):
        self.case = BenchmarkCase(
            "sample",
            "implementation",
            "Return range, length, and index.",
            ("range", "length", "index"),
        )

    def test_dry_run_is_default_and_never_calls_provider(self):
        provider = Mock()
        runner = BenchmarkRunner(provider=provider, cases=(self.case,))

        report = runner.run(("model-a", "model-b"))

        self.assertTrue(report.dry_run)
        self.assertEqual(len(report.records), 2)
        self.assertTrue(
            all(record.status is BenchmarkStatus.DRY_RUN for record in report.records)
        )
        self.assertEqual(report.tier_mappings, {})
        provider.assert_not_called()

    def test_live_opt_in_records_quality_latency_tokens_and_cost(self):
        provider = Mock(
            return_value=BenchmarkResponse(
                text="Use range based on sequence length before each index.",
                input_tokens=100,
                output_tokens=50,
            )
        )
        clock = iter((10.0, 10.4))
        runner = BenchmarkRunner(
            provider=provider,
            cases=(self.case,),
            clock=lambda: next(clock),
        )
        model = ModelSpec(
            "model-a",
            input_cost_per_million=2.0,
            output_cost_per_million=4.0,
        )

        report = runner.run((model,), allow_paid=True)

        self.assertFalse(report.dry_run)
        record = report.records[0]
        self.assertEqual(record.status, BenchmarkStatus.COMPLETED)
        self.assertEqual(record.quality_proxy, 1.0)
        self.assertAlmostEqual(record.latency_seconds, 0.4)
        self.assertEqual(record.input_tokens, 100)
        self.assertEqual(record.output_tokens, 50)
        self.assertAlmostEqual(record.estimated_cost_usd, 0.0004)
        self.assertEqual(
            report.tier_mappings,
            {"cheap": "model-a", "standard": "model-a", "strong": "model-a"},
        )
        provider.assert_called_once_with("model-a", self.case)

    def test_live_alias_is_an_explicit_opt_in(self):
        provider = Mock(
            return_value={"content": "range length index", "usage": {
                "input_tokens": 3,
                "output_tokens": 3,
            }}
        )
        runner = BenchmarkRunner(provider=provider, cases=(self.case,))

        report = runner.run(("model-a",), live=True)

        self.assertFalse(report.dry_run)
        provider.assert_called_once()

    def test_live_run_requires_provider(self):
        runner = BenchmarkRunner(cases=(self.case,))
        with self.assertRaises(RuntimeError):
            runner.run(("model-a",), allow_paid=True)

    def test_provider_failure_is_recorded_and_redacted(self):
        def provider(model, case):
            raise RuntimeError("api_key=supersecret")

        clock = iter((1.0, 1.2))
        runner = BenchmarkRunner(
            provider=provider,
            cases=(self.case,),
            clock=lambda: next(clock),
        )

        report = runner.run(("model-a",), allow_paid=True)

        record = report.records[0]
        self.assertEqual(record.status, BenchmarkStatus.FAILED)
        self.assertNotIn("supersecret", record.error)
        self.assertEqual(report.tier_mappings, {})


class TestBenchmarkMetrics(unittest.TestCase):
    def test_cost_requires_both_prices(self):
        response = BenchmarkResponse("text", input_tokens=10, output_tokens=20)
        self.assertIsNone(estimate_cost(ModelSpec("unknown"), response))
        self.assertIsNone(
            estimate_cost(
                ModelSpec("partial", input_cost_per_million=1.0),
                response,
            )
        )

    def test_recommends_expected_tiers_from_measured_records(self):
        records = (
            BenchmarkRecord(
                "economy",
                "case",
                "implementation",
                BenchmarkStatus.COMPLETED,
                quality_proxy=0.70,
                latency_seconds=0.5,
                input_tokens=10,
                output_tokens=10,
                estimated_cost_usd=0.001,
            ),
            BenchmarkRecord(
                "balanced",
                "case",
                "implementation",
                BenchmarkStatus.COMPLETED,
                quality_proxy=0.85,
                latency_seconds=1.0,
                input_tokens=10,
                output_tokens=10,
                estimated_cost_usd=0.010,
            ),
            BenchmarkRecord(
                "premium",
                "case",
                "implementation",
                BenchmarkStatus.COMPLETED,
                quality_proxy=1.0,
                latency_seconds=2.0,
                input_tokens=10,
                output_tokens=10,
                estimated_cost_usd=0.050,
            ),
        )

        mappings = recommend_tier_mappings(records)

        self.assertEqual(
            mappings,
            {
                "cheap": "economy",
                "standard": "balanced",
                "strong": "premium",
            },
        )


if __name__ == "__main__":
    unittest.main()

