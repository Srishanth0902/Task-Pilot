import unittest

from streamlit.testing.v1 import AppTest

from app.config import PROJECT_ROOT


class StreamlitAppTests(unittest.TestCase):
    def test_initial_page_renders_without_contacting_backend(self):
        app = AppTest.from_file(str(PROJECT_ROOT / "streamlit_app.py"))

        app.run(timeout=15)

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.title[0].value, "📅 Agentic Calendar Assistant")
        self.assertEqual(len(app.chat_input), 1)
        labels = [button.label for button in app.button]
        self.assertIn("Check backend", labels)
        self.assertIn("Refresh upcoming events", labels)


if __name__ == "__main__":
    unittest.main()
