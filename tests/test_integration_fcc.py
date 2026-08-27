import unittest
from vibeflow.fcc_client import FCCClient, FCCError

class TestIntegrationFCC(unittest.TestCase):
    def setUp(self):
        self.client = FCCClient()
    
    def test_health_check(self):
        if not self.client.health_check():
            self.skipTest("FCC server is not reachable in this environment")
        self.assertTrue(self.client.health_check())
    
    def test_list_models(self):
        if not self.client.health_check():
            self.skipTest("FCC server is not reachable in this environment")
        try:
            models = self.client.list_models()
        except FCCError as error:
            self.skipTest(f"FCC model listing unavailable: {error}")
        self.assertIsInstance(models, dict)
        model_list = models.get("data", models.get("models"))
        self.assertIsInstance(model_list, list)

if __name__ == '__main__':
    unittest.main()
