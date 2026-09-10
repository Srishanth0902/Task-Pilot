import unittest

from app.llm import create_openrouter_model


class OpenRouterModelTests(unittest.TestCase):
    def test_builds_qwen_model_with_openrouter_endpoint(self):
        model = create_openrouter_model(api_key="test-key")

        self.assertEqual(model.model_name, "qwen/qwen3-30b-a3b")
        self.assertEqual(str(model.openai_api_base), "https://openrouter.ai/api/v1")
        self.assertEqual(model.temperature, 0.0)

    def test_model_can_be_switched_without_agent_changes(self):
        model = create_openrouter_model(
            api_key="test-key", model_name="provider/another-model"
        )

        self.assertEqual(model.model_name, "provider/another-model")

    def test_requires_an_api_key(self):
        with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
            create_openrouter_model(api_key="")


if __name__ == "__main__":
    unittest.main()
