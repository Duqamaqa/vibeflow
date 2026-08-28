import unittest
from vibeflow.contracts import ApprovalState, Contract, contract_from_request

class TestContract(unittest.TestCase):
    def test_research_contract_requires_verified_non_fabricated_evidence(self):
        contract = contract_from_request("Find prospects", task_type="research")

        self.assertIn("live, attributable evidence", contract.acceptance_criteria[0])
        self.assertIn("does not fabricate", contract.acceptance_criteria[0])

    def test_combined_research_contract_also_requires_verified_code(self):
        contract = contract_from_request(
            "Research a business and build its website",
            task_type="research-and-implementation",
        )

        self.assertEqual(len(contract.acceptance_criteria), 2)
        self.assertIn("live, attributable evidence", contract.acceptance_criteria[0])
        self.assertIn("deterministic verification", contract.acceptance_criteria[1])

    def test_contract_creation(self):
        contract = Contract(
            goal="Test goal",
            constraints=["C1", "C2"],
            acceptance_criteria=["AC1"],
            non_goals=["NG1"],
            risk="medium",
            ambiguity="high"
        )
        self.assertEqual(contract.goal, "Test goal")
        self.assertEqual(contract.constraints, ["C1", "C2"])
        self.assertEqual(contract.acceptance_criteria, ["AC1"])
        self.assertEqual(contract.non_goals, ["NG1"])
        self.assertEqual(contract.risk, "medium")
        self.assertEqual(contract.ambiguity, "high")
    
    def test_is_clear_and_low_risk(self):
        # Clear and low risk
        contract = Contract(goal="G", acceptance_criteria=["AC"])
        self.assertTrue(contract.is_clear_and_low_risk())
        
        # Not clear (no goal)
        contract = Contract(goal="", acceptance_criteria=["AC"])
        self.assertFalse(contract.is_clear_and_low_risk())
        
        # Not low risk
        contract = Contract(goal="G", acceptance_criteria=["AC"], risk="high")
        self.assertFalse(contract.is_clear_and_low_risk())
        
        # High ambiguity
        contract = Contract(goal="G", acceptance_criteria=["AC"], ambiguity="high")
        self.assertFalse(contract.is_clear_and_low_risk())
    
    def test_requires_user_approval(self):
        # Clear and low risk -> no user approval required
        contract = Contract(goal="G", acceptance_criteria=["AC"])
        self.assertFalse(contract.requires_user_approval())
        
        # Otherwise -> user approval required
        contract = Contract(goal="G")  # missing acceptance criteria
        self.assertTrue(contract.requires_user_approval())
        
        contract = Contract(goal="G", acceptance_criteria=["AC"], risk="high")
        self.assertTrue(contract.requires_user_approval())
        
        contract = Contract(goal="G", acceptance_criteria=["AC"], ambiguity="medium")
        self.assertTrue(contract.requires_user_approval())

    def test_trivial_clear_request_gets_default_acceptance_criterion(self):
        contract = contract_from_request("Rename one local variable")
        self.assertEqual(contract.approval_state(), ApprovalState.AUTO_APPROVED)
        self.assertTrue(contract.acceptance_criteria)

    def test_reverse_prompting_only_for_material_ambiguity(self):
        clear = Contract("G", acceptance_criteria=["AC"])
        ambiguous = Contract("G", acceptance_criteria=["AC"], ambiguity="high")
        self.assertEqual(clear.reverse_questions(), [])
        self.assertTrue(ambiguous.reverse_questions())
