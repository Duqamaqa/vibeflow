from types import SimpleNamespace
import unittest

from vibeflow.agent import AgentMessage, AgentRequest
from vibeflow.agent_execution import FCCAgentExecutor


class FakeClient:
    def __init__(self):
        self.kwargs = None

    def create_response(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            text="ship it",
            usage={"input_tokens": 10, "output_tokens": 2},
            request_id="req-1",
        )


class TestFCCAgentExecutor(unittest.TestCase):
    def test_isolated_request_is_normalized(self):
        client = FakeClient()
        executor = FCCAgentExecutor(client, "test/model")
        result = executor.execute(AgentRequest(
            prompt="decide",
            role="contrarian",
            system_prompt="challenge assumptions",
            history=(AgentMessage("assistant", "prior private turn"),),
        ))
        self.assertEqual(result.decision, "ship it")
        self.assertEqual(result.usage.input_tokens, 10)
        self.assertEqual(client.kwargs["model"], "test/model")
        self.assertIn("prior private turn", client.kwargs["input"])


if __name__ == "__main__":
    unittest.main()
