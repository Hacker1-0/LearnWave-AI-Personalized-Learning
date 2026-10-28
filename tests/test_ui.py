import unittest
import os
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from backend.learning_engine import GenerationError
from backend.local_ai import configured_local_model


class DashboardTests(unittest.TestCase):
    def test_remote_demo_hides_key_setup(self):
        app_path = Path(__file__).resolve().parents[1] / "frontend" / "app.py"
        with patch.dict(os.environ, {"LEARNWAVE_REMOTE_DEMO": "1", "LEARNWAVE_REQUIRE_LOGIN": "false"}):
            app = AppTest.from_file(str(app_path), default_timeout=20).run()
            app.button(key="nav_Settings").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertFalse(any(widget.key == "local_gemini_key_input" for widget in app.text_input))
            self.assertFalse(any(button.key == "check_gemini" for button in app.button))

    def test_navigation_generation_cache_and_previous_answer(self):
        app_path = Path(__file__).resolve().parents[1] / "frontend" / "app.py"
        result = {"text": "## Binary search\nSearch a sorted list by repeatedly halving it.", "source": "local", "model": configured_local_model(), "prompt": "test"}
        with patch("backend.local_ai.local_models", return_value=(configured_local_model(),)), patch("backend.learning_engine.generate_content", return_value=result) as generate:
            app = AppTest.from_file(str(app_path), default_timeout=20).run()
            self.assertEqual(len(app.exception), 0)
            for label in ("Home", "Courses", "AI Assistant", "Notes & Flashcards", "Settings"):
                self.assertTrue(app.button(key=f"nav_{label}").proto.icon)
            app.button(key="sidebar_generate").click().run()
            self.assertEqual(len(app.exception), 0)
            app.selectbox(key="generation_mode").select("Offline AI (no internet)").run()
            topic = next(widget for widget in app.text_input if widget.label == "Topic")
            topic.set_value("Binary search")
            next(button for button in app.button if "Generate for me" in button.label).click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.session_state["result"]["source"], "local")
            self.assertEqual(generate.call_args.kwargs["api_key"], "")
            next(button for button in app.button if "Generate for me" in button.label).click().run()
            self.assertTrue(app.session_state["result"]["cached"])
            self.assertEqual(generate.call_count, 1)
            generate.side_effect = GenerationError("Online AI is temporarily unavailable.")
            next(widget for widget in app.text_input if widget.label == "Topic").set_value("A different topic")
            next(button for button in app.button if "Generate for me" in button.label).click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any("unavailable" in item.value for item in app.warning))
            self.assertEqual(app.session_state["result"]["topic"], "Binary search")


if __name__ == "__main__":
    unittest.main()
