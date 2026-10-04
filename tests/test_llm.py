import unittest
from unittest.mock import patch, Mock

from app.llm import create_openrouter_model


class OpenRouterModelTests(unittest.TestCase):
    def test_agent_uses_same_schema_format_as_live_smoke(self):
        from app.graph_agent import create_calendar_graph, QueryPlan
        from tests.test_calendar_service import FakeService
        model = Mock()
        create_calendar_graph(model, FakeService())
        model.with_structured_output.assert_called_once_with(
            QueryPlan, method='json_schema', include_raw=True)

    def test_builds_free_qwen_with_openrouter_endpoint(self):
        model = create_openrouter_model(api_key="test-key")

        self.assertEqual(model.model_name, "qwen/qwen3.8-27b:free")
        self.assertEqual(model.max_retries, 0)
        self.assertEqual(model.extra_body['provider']['data_collection'], 'deny')
        self.assertEqual(model.extra_body['provider']['max_price'], {'prompt': 0, 'completion': 0})
        self.assertEqual(str(model.openai_api_base), "https://openrouter.ai/api/v1")
        self.assertEqual(model.temperature, 0.0)

    def test_model_can_be_switched_without_agent_changes(self):
        model = create_openrouter_model(
            api_key="test-key", model_name="provider/another-model:free"
        )

        self.assertEqual(model.model_name, "provider/another-model:free")

    def test_paid_model_is_blocked_in_free_mode(self):
        with self.assertRaisesRegex(ValueError, 'Free mode'):
            create_openrouter_model(api_key='test', model_name='qwen/qwen3-30b-a3b')

    def test_paid_mode_requires_explicit_opt_in(self):
        with patch.dict('os.environ', {'OPENROUTER_FREE_ONLY': 'false'}):
            model = create_openrouter_model(api_key='test', model_name='provider/paid')
        self.assertEqual(model.max_retries, 2)
        self.assertEqual(model.extra_body['provider']['data_collection'], 'deny')

    def test_requires_an_api_key(self):
        with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
            create_openrouter_model(api_key="")


if __name__ == "__main__":
    unittest.main()
