import asyncio
import json
import unittest

from lex.lex_app.fast_health import health_asgi_app, is_fast_health_path, match_health_request_path


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


class MatchHealthRequestPathTests(unittest.TestCase):
    # --- bare paths (no query string) ---

    def test_bare_health_path_matches(self):
        self.assertTrue(match_health_request_path("/health"))

    def test_bare_health_trailing_slash_matches(self):
        self.assertTrue(match_health_request_path("/health/"))

    def test_bare_api_health_path_matches(self):
        self.assertTrue(match_health_request_path("/api/health"))

    def test_bare_api_health_trailing_slash_matches(self):
        self.assertTrue(match_health_request_path("/api/health/"))

    # --- query-string variants ---

    def test_health_with_query_string_matches(self):
        self.assertTrue(match_health_request_path("/health?source=k8s"))

    def test_health_trailing_slash_with_query_string_matches(self):
        self.assertTrue(match_health_request_path("/health/?check=ready"))

    def test_api_health_with_query_string_matches(self):
        self.assertTrue(match_health_request_path("/api/health?source=gcp"))

    def test_api_health_trailing_slash_with_query_string_matches(self):
        self.assertTrue(match_health_request_path("/api/health/?check=ready"))

    def test_health_with_multiple_query_params_matches(self):
        self.assertTrue(match_health_request_path("/health?a=1&b=2"))

    def test_health_with_question_mark_only_matches(self):
        # Edge: bare "?" with no key/value — path portion is still "/health"
        self.assertTrue(match_health_request_path("/health?"))

    # --- non-health paths ---

    def test_non_health_path_does_not_match(self):
        self.assertFalse(match_health_request_path("/api/user/"))

    def test_non_health_path_with_query_string_does_not_match(self):
        self.assertFalse(match_health_request_path("/users?check=ready"))

    def test_root_path_does_not_match(self):
        self.assertFalse(match_health_request_path("/"))

    def test_empty_string_does_not_match(self):
        self.assertFalse(match_health_request_path(""))
