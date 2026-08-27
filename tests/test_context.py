import tempfile
from pathlib import Path
import unittest

from vibeflow.context import ContextError, ContextManager
from vibeflow.contracts import Contract


class TestContextManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / ".ai").mkdir()
        (self.root / ".ai" / "architecture.md").write_text("architecture " * 50, encoding="utf-8")
        (self.root / ".ai" / "coding_rules.md").write_text("rules " * 50, encoding="utf-8")
        (self.root / "active.py").write_text("print('active')", encoding="utf-8")
        (self.root / "unrequested-secret.txt").write_text("never dump me", encoding="utf-8")
        self.manager = ContextManager(self.root)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_context_trims_low_value_items_first(self):
        contract = Contract("Change active file", acceptance_criteria=["tests pass"])
        bundle = self.manager.build_context(contract, ["active.py"], max_tokens=120)
        self.assertLessEqual(bundle.estimated_tokens, 120)
        self.assertEqual(bundle.items[0].kind, "contract")
        self.assertNotIn("never dump me", bundle.render())

    def test_paths_cannot_escape_repository(self):
        with self.assertRaises(ContextError):
            self.manager.read_file(self.root.parent / "outside.txt")


if __name__ == "__main__":
    unittest.main()
