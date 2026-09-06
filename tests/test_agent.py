import unittest

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.agent import create_calendar_agent, run_calendar_request
from tests.test_calendar_service import FakeService


class ToolCallingFakeModel(FakeMessagesListChatModel):
    """Scripted model that supports tool binding without a provider or key."""

    bound_tool_names: list[str] = []

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        self.bound_tool_names = [tool.name for tool in tools]
        return self


class AgentTests(unittest.TestCase):
    def test_fake_llm_selects_and_executes_create_tool(self):
        service = FakeService()
        model = ToolCallingFakeModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "create_calendar_event",
                            "args": {
                                "title": "ML study",
                                "start_time": "2026-08-25T18:00:00+05:30",
                            },
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="The event was created."),
            ]
        )

        result = run_calendar_request(
            "Add ML study tomorrow at 6 PM", model=model, service=service
        )

        self.assertIn("create_calendar_event", model.bound_tool_names)
        self.assertEqual(service.events().calls[0][0], "insert")
        self.assertEqual(
            service.events().calls[0][1]["body"]["summary"], "ML study"
        )
        self.assertEqual(result["messages"][-1].content, "The event was created.")

    def test_agent_requires_an_injected_model(self):
        with self.assertRaisesRegex(ValueError, "chat model"):
            create_calendar_agent(None, FakeService())
