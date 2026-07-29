import asyncio
import json
import unittest

from lex.lex_app.fast_health import health_asgi_app, is_fast_health_path


class FastHealthTests(unittest.TestCase):
    def test_is_fast_health_path(self):
        self.assertTrue(is_fast_health_path("/health"))
        self.assertTrue(is_fast_health_path("/health/"))
        self.assertTrue(is_fast_health_path("/api/health"))
        self.assertTrue(is_fast_health_path("/api/health/"))
        self.assertFalse(is_fast_health_path("/api/user/"))

    def test_health_asgi_app_returns_json(self):
        async def run():
            inbound_messages = [
                {"type": "http.request", "body": b"", "more_body": False},
            ]
            outbound_messages = []

            async def receive():
                if inbound_messages:
                    return inbound_messages.pop(0)
                return {"type": "http.disconnect"}

            async def send(message):
                outbound_messages.append(message)

            await health_asgi_app({"type": "http", "path": "/health"}, receive, send)
            return outbound_messages

        sent = asyncio.run(run())
        self.assertEqual(sent[0]["type"], "http.response.start")
        self.assertEqual(sent[0]["status"], 200)
        self.assertEqual(dict(sent[0]["headers"])[b"content-type"], b"application/json")

        self.assertEqual(sent[1]["type"], "http.response.body")
        payload = json.loads(sent[1]["body"].decode("utf-8"))
        self.assertEqual(payload, {"status": "Healthy :)"})
