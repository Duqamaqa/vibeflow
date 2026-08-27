import unittest

from vibeflow.contracts import Contract
from vibeflow.decomposition import DecompositionError, TaskDecomposer


class TestTaskDecomposer(unittest.TestCase):
    def setUp(self):
        self.contract = Contract("Build feature", acceptance_criteria=["tests pass"])
        self.decomposer = TaskDecomposer()

    def test_safe_default_is_one_coherent_task(self):
        graph = self.decomposer.decompose(self.contract)
        self.assertEqual(len(graph.units), 1)
        self.assertFalse(graph.units[0].parallelizable)

    def test_dependencies_are_topologically_ordered(self):
        graph = self.decomposer.decompose(self.contract, [
            {"id": "tests", "goal": "Add tests", "acceptance_criteria": ["red test"], "dependencies": ["core"]},
            {"id": "core", "goal": "Add core", "acceptance_criteria": ["implemented"]},
        ])
        self.assertEqual([unit.unit_id for unit in graph.topological_order()], ["core", "tests"])

    def test_parallel_units_cannot_overlap_write_scope(self):
        with self.assertRaises(DecompositionError):
            self.decomposer.decompose(self.contract, [
                {"id": "a", "goal": "A", "acceptance_criteria": ["A"], "active_files": ["same.py"], "parallelizable": True},
                {"id": "b", "goal": "B", "acceptance_criteria": ["B"], "active_files": ["same.py"], "parallelizable": True},
            ])


if __name__ == "__main__":
    unittest.main()
