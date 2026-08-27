import unittest

from src.vibeflow.agent import AgentRequest, AgentResult
from src.vibeflow.debate import DebateRole, DebateStrategy


class RecordingExecutor:
    def __init__(self):
        self.requests = []

    def execute(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        if request.role == "judge":
            return AgentResult("final synthesis")
        return AgentResult(
            f"{request.role}-round-{request.metadata['round']}"
        )


class TestDebateStrategy(unittest.TestCase):
    def setUp(self):
        self.roles = (
            DebateRole("builder", "Argue for the practical implementation."),
            DebateRole("critic", "Challenge risks and assumptions."),
        )

    def test_role_histories_stay_separate_across_rounds(self):
        executor = RecordingExecutor()
        debate = DebateStrategy(executor, roles=self.roles, max_rounds=2)

        result = debate.run("Select an architecture")

        builder_round_two = executor.requests[2]
        critic_round_two = executor.requests[3]
        self.assertEqual(len(builder_round_two.history), 2)
        self.assertEqual(len(critic_round_two.history), 2)
        self.assertIn("builder-round-1", builder_round_two.history[1].content)
        self.assertNotIn("critic-round-1", builder_round_two.history[1].content)
        self.assertIn("critic-round-1", critic_round_two.history[1].content)
        self.assertNotIn("builder-round-1", critic_round_two.history[1].content)
        self.assertEqual(len(result.role_histories["builder"]), 4)
        self.assertEqual(len(result.role_histories["critic"]), 4)

    def test_rounds_use_completed_public_transcript_only(self):
        executor = RecordingExecutor()
        debate = DebateStrategy(executor, roles=self.roles, max_rounds=2)

        debate.run("Select an architecture")

        self.assertNotIn("builder-round-1", executor.requests[1].prompt)
        self.assertIn("builder-round-1", executor.requests[2].prompt)
        self.assertIn("critic-round-1", executor.requests[2].prompt)

    def test_final_judge_receives_complete_transcript_and_no_role_history(self):
        executor = RecordingExecutor()
        debate = DebateStrategy(executor, roles=self.roles, max_rounds=2)

        result = debate.run("Select an architecture", rounds=1)

        judge_request = executor.requests[-1]
        self.assertEqual(judge_request.role, "judge")
        self.assertEqual(judge_request.history, ())
        self.assertIn("builder-round-1", judge_request.prompt)
        self.assertIn("critic-round-1", judge_request.prompt)
        self.assertEqual(result.final_answer, "final synthesis")
        self.assertEqual(result.rounds_completed, 1)

    def test_requested_rounds_cannot_exceed_bound(self):
        executor = RecordingExecutor()
        debate = DebateStrategy(executor, roles=self.roles, max_rounds=2)

        with self.assertRaises(ValueError):
            debate.run("Select an architecture", rounds=3)

        self.assertEqual(executor.requests, [])

    def test_role_and_judge_executors_can_be_injected_independently(self):
        builder = RecordingExecutor()
        critic = RecordingExecutor()
        judge = RecordingExecutor()
        debate = DebateStrategy(
            {"builder": builder, "critic": critic},
            judge_executor=judge,
            roles=self.roles,
            max_rounds=1,
        )

        result = debate.run("Select an architecture")

        self.assertEqual(len(builder.requests), 1)
        self.assertEqual(len(critic.requests), 1)
        self.assertEqual(len(judge.requests), 1)
        self.assertEqual(result.final_answer, "final synthesis")


if __name__ == "__main__":
    unittest.main()

