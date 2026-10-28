import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend import learning_engine as engine
from backend import local_ai


class ProviderFailure(Exception):
    def __init__(self, code):
        self.code = code


class GenerationTests(unittest.TestCase):
    def call(self, **overrides):
        values = dict(topic="Binary search", content_type="Explanation", level="Beginner",
                      quiz_score=None, api_key="test-key", model="gemini-flash-latest", provider="gemini")
        values.update(overrides)
        return engine.generate_content(**values)

    def client(self, outcomes):
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.models.generate_content.side_effect = outcomes
        return client

    def test_retry_then_distinct_backup(self):
        response = SimpleNamespace(text="A complete answer.", candidates=[])
        client = self.client([ProviderFailure(503), ProviderFailure(503), response])
        with patch("google.genai.Client", return_value=client), patch.object(engine.time, "sleep"):
            result = self.call()
        self.assertEqual(result["model"], "gemini-flash-lite-latest")
        self.assertEqual(client.models.generate_content.call_count, 3)

    def test_invalid_credentials_and_quota_are_not_retried(self):
        for code in (400, 401, 403, 429):
            client = self.client([ProviderFailure(code)])
            with self.subTest(code=code), patch("google.genai.Client", return_value=client):
                with self.assertRaises(engine.GenerationError):
                    self.call()
                self.assertEqual(client.models.generate_content.call_count, 1)

    def test_exhausted_recovery_gives_actionable_message(self):
        client = self.client([ProviderFailure(503)] * 3)
        with patch("google.genai.Client", return_value=client), patch.object(engine.time, "sleep"):
            with self.assertRaisesRegex(engine.GenerationError, "Offline AI"):
                self.call()

    def test_truncated_answer_is_not_marked_complete(self):
        response = SimpleNamespace(text="Half an answer", candidates=[SimpleNamespace(finish_reason="MAX_TOKENS")])
        with patch("google.genai.Client", return_value=self.client([response])):
            with self.assertRaisesRegex(engine.GenerationError, "cut short"):
                self.call()

    def test_offline_never_calls_cloud_even_when_it_fails(self):
        with patch.object(engine, "generate_local", side_effect=local_ai.LocalAIError("Unavailable")), patch("google.genai.Client") as cloud:
            with self.assertRaises(local_ai.LocalAIError):
                self.call(provider="local")
            cloud.assert_not_called()

    def test_auto_prefers_local_ai(self):
        with patch.object(engine, "local_models", return_value=(local_ai.configured_local_model(),)), patch.object(engine, "generate_local", return_value={"source": "local"}), patch("google.genai.Client") as cloud:
            self.assertEqual(self.call(provider="auto")["source"], "local")
            cloud.assert_not_called()

    def test_local_stream_requires_completion(self):
        for complete in (True, False):
            packets = [{"response": "A local answer", "done": False}]
            if complete:
                packets.append({"done": True, "done_reason": "stop"})
            body = io.BytesIO(b"\n".join(json.dumps(item).encode() for item in packets))
            opener = Mock()
            opener.open.return_value = body
            with self.subTest(complete=complete), patch.object(local_ai, "local_models", return_value=(local_ai.configured_local_model(),)), patch.object(local_ai, "build_opener", return_value=opener):
                if complete:
                    self.assertEqual(local_ai.generate_local("test")["text"], "A local answer")
                else:
                    with self.assertRaises(local_ai.LocalAIError):
                        local_ai.generate_local("test")
            self.assertTrue(local_ai._MODEL_SLOT.acquire(blocking=False))
            local_ai._MODEL_SLOT.release()

    def test_difficulty_boundaries_and_prompt_context(self):
        for start, score, expected in [("Beginner", 49, "Beginner"), ("Intermediate", 49, "Beginner"), ("Intermediate", 50, "Intermediate"), ("Intermediate", 79, "Intermediate"), ("Intermediate", 80, "Advanced"), ("Advanced", 100, "Advanced")]:
            self.assertEqual(engine.recommend_level(start, score)[0], expected)
        prompt = engine.build_prompt("CPU scheduling", "Practice questions", "Advanced", 85)
        self.assertIn("85%", prompt)
        self.assertIn("Create 5 questions", prompt)
        self.assertIn("Advanced", prompt)


if __name__ == "__main__":
    unittest.main()
