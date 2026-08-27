import unittest

from src.vibeflow.agent import AgentRequest, AgentResult
from src.vibeflow.consensus import ConsensusPolicy, ConsensusStrategy


class QueueExecutor:
    def __init__(self, results):
        self.results = list(results)
        self.requests = []

    def execute(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        return self.results.pop(0)


class TestConsensusStrategy(unittest.TestCase):
    def test_below_threshold_does_not_execute_agents(self):
        executor = QueueExecutor([AgentResult("unused")])
        strategy = ConsensusStrategy(executor, agent_count=3)

        result = strategy.run("Choose an API", uncertainty=0.2, value=0.3)

        self.assertFalse(result.triggered)
        self.assertEqual(result.reason, "below-threshold")
        self.assertEqual(executor.requests, [])
        self.assertEqual(result.responses, ())

    def test_high_uncertainty_or_high_value_triggers(self):
        for uncertainty, value in ((0.7, 0.1), (0.1, 0.8)):
            with self.subTest(uncertainty=uncertainty, value=value):
                executor = QueueExecutor(
                    [
                        AgentResult("yes", decision="ship"),
                        AgentResult("yes", decision="ship"),
                        AgentResult("no", decision="wait"),
                    ]
                )
                result = ConsensusStrategy(executor, agent_count=3).run(
                    "Make a decision",
                    uncertainty=uncertainty,
                    value=value,
                )
                self.assertTrue(result.triggered)
                self.assertEqual(len(executor.requests), 3)

    def test_each_agent_gets_an_independent_prompt_and_empty_history(self):
        executor = QueueExecutor([AgentResult("same")] * 3)
        strategy = ConsensusStrategy(executor, agent_count=3)

        strategy.run("Evaluate the design", uncertainty=0.9, value=0.1)

        prompts = [request.prompt for request in executor.requests]
        self.assertEqual(len(set(prompts)), 3)
        self.assertTrue(all(request.history == () for request in executor.requests))
        self.assertTrue(all("same" not in request.prompt for request in executor.requests))
        self.assertEqual(
            [request.role for request in executor.requests],
            ["consensus-agent-1", "consensus-agent-2", "consensus-agent-3"],
        )

    def test_aggregation_is_normalized_deterministic_and_extracts_outlier(self):
        executor = QueueExecutor(
            [
                AgentResult("first rationale", decision="Ship now"),
                AgentResult("second rationale", decision="  SHIP   NOW "),
                AgentResult("risk rationale", decision="Wait"),
            ]
        )

        result = ConsensusStrategy(executor, agent_count=3).run(
            "Choose release timing",
            uncertainty=0.9,
            value=0.9,
        )

        self.assertEqual(result.consensus, "Ship now")
        self.assertEqual(result.disagreements, ("Wait",))
        self.assertEqual(result.positions[0].voters, (
            "consensus-agent-1",
            "consensus-agent-2",
        ))
        self.assertEqual(result.agreement_ratio, 2 / 3)
        self.assertEqual(
            [response.agent_id for response in result.outliers],
            ["consensus-agent-3"],
        )

    def test_no_majority_reports_all_positions_as_disagreements(self):
        executor = QueueExecutor(
            [
                AgentResult("b", decision="Beta"),
                AgentResult("a", decision="Alpha"),
                AgentResult("g", decision="Gamma"),
            ]
        )

        result = ConsensusStrategy(executor, agent_count=3).run(
            "Pick one",
            uncertainty=1.0,
            value=0.0,
        )

        self.assertIsNone(result.consensus)
        self.assertEqual(result.disagreements, ("Alpha", "Beta", "Gamma"))
        self.assertEqual(result.outliers, ())

    def test_policy_can_require_both_thresholds(self):
        policy = ConsensusPolicy(trigger_mode="both")
        self.assertFalse(policy.should_run(uncertainty=0.9, value=0.1))
        self.assertTrue(policy.should_run(uncertainty=0.9, value=0.9))


if __name__ == "__main__":
    unittest.main()

