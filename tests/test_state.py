import tempfile
from pathlib import Path
import unittest

from vibeflow.state import TaskState, TaskStateStore


class TestTaskStateStore(unittest.TestCase):
    def test_round_trip_and_corrupt_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".ai" / "state.json"
            store = TaskStateStore(path)
            state = TaskState("task", "running", "goal", "cheap", "review", 1.0)
            store.save(state)
            self.assertEqual(store.load(), state)
            path.write_text("not json", encoding="utf-8")
            self.assertIsNone(store.load())


if __name__ == "__main__":
    unittest.main()
