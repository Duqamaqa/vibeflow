import unittest
from decimal import Decimal

from src.vibeflow.budget import (
    BudgetExceeded,
    BudgetLedger,
    BudgetPolicy,
    CostEstimator,
    ModelPricing,
    PricingTable,
    estimate_cost,
)


class TestBudget(unittest.TestCase):
    def test_empty_table_never_invents_a_price(self):
        table = PricingTable()

        self.assertEqual(len(table), 0)
        self.assertIsNone(
            CostEstimator(table).estimate(
                "unknown/model",
                {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
            )
        )
        self.assertIsNone(
            estimate_cost(
                "unknown/model",
                {"input_tokens": 1, "output_tokens": 1},
                table,
            )
        )

    def test_configured_prices_are_estimated_with_decimal_math(self):
        estimator = CostEstimator(
            {
                "provider/model": ModelPricing(
                    input_per_million=Decimal("2"),
                    output_per_million=Decimal("6"),
                )
            }
        )

        estimate = estimator.estimate(
            "provider/model",
            {"input_tokens": 1_000_000, "output_tokens": 500_000},
        )

        self.assertIsNotNone(estimate)
        self.assertEqual(estimate.input_cost, Decimal("2"))
        self.assertEqual(estimate.output_cost, Decimal("3"))
        self.assertEqual(estimate.total_cost, Decimal("5"))
        self.assertEqual(estimate.total_cost_usd, Decimal("5"))

    def test_mapping_prices_and_legacy_usage_names(self):
        table = PricingTable(
            {
                "provider/model": {
                    "input_per_million_tokens": "1.5",
                    "output_per_million_tokens": "3",
                }
            }
        )

        estimate = CostEstimator(table).estimate(
            "provider/model",
            {"prompt_tokens": 2_000, "completion_tokens": 1_000},
        )

        self.assertEqual(estimate.input_tokens, 2_000)
        self.assertEqual(estimate.output_tokens, 1_000)
        self.assertEqual(estimate.total_cost, Decimal("0.006"))

    def test_invalid_prices_and_usage_are_rejected(self):
        with self.assertRaises(ValueError):
            ModelPricing(input_per_million="-1", output_per_million="2")

        estimator = CostEstimator(
            {
                "provider/model": {
                    "input_per_million": "1",
                    "output_per_million": "1",
                }
            }
        )
        with self.assertRaises(ValueError):
            estimator.estimate(
                "provider/model",
                {"input_tokens": -1, "output_tokens": 0},
            )

    def test_budget_ledger_enforces_requests_tokens_and_unknown_cost(self):
        ledger = BudgetLedger(BudgetPolicy(max_requests=1, max_total_tokens=10))
        ledger.before_request()
        snapshot = ledger.record(
            "unknown/model", {"input_tokens": 4, "output_tokens": 2}
        )
        self.assertEqual(snapshot.requests, 1)
        self.assertEqual(snapshot.unpriced_requests, 1)
        with self.assertRaises(BudgetExceeded):
            ledger.before_request()

        strict = BudgetLedger(BudgetPolicy(
            max_estimated_cost_usd=Decimal("1"), allow_unknown_cost=False
        ))
        with self.assertRaises(BudgetExceeded):
            strict.record("unknown/model", {"input_tokens": 1, "output_tokens": 1})


if __name__ == "__main__":
    unittest.main()
