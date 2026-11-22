import json
import unittest

import httpx

from app.config import Settings
from app.llm import GeminiClient, ModelError, SYSTEM_PROMPT


class GeminiTests(unittest.IsolatedAsyncioTestCase):
    async def call_model(self, handler, api_key="test-api-key"):
        settings = Settings(access_key="test-access-123", gemini_api_key=api_key)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await GeminiClient(settings, client).answer("Вопрос")

    async def test_request_and_response(self):
        def handler(request):
            self.assertEqual(request.headers["x-goog-api-key"], "test-api-key")
            self.assertNotIn("test-api-key", str(request.url))
            self.assertTrue(request.url.path.endswith(":generateContent"))
            payload = json.loads(request.content)
            self.assertEqual(payload["systemInstruction"]["parts"][0]["text"], SYSTEM_PROMPT)
            self.assertEqual(payload["contents"], [{"role": "user", "parts": [{"text": "Вопрос"}]}])
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "hidden", "thought": True}, {"text": "Ответ"}]}}]})
        self.assertEqual(await self.call_model(handler), "Ответ")

    async def test_missing_api_key_does_not_call_provider(self):
        def handler(request):
            self.fail("Provider should not be called without a key")
        with self.assertRaises(ModelError) as caught:
            await self.call_model(handler, api_key="")
        self.assertEqual(caught.exception.status_code, 503)

    async def test_provider_errors(self):
        for status in (400, 401, 403, 404, 429, 500, 503):
            with self.subTest(status=status):
                with self.assertRaises(ModelError) as caught:
                    await self.call_model(lambda request: httpx.Response(status, json={"error": "private detail"}))
                self.assertIn(caught.exception.status_code, (502, 503))
                self.assertNotIn("private detail", str(caught.exception))

    async def test_empty_and_malformed_responses(self):
        for payload in ({}, {"candidates": []}, {"candidates": [{"finishReason": "SAFETY"}]}, [], {"candidates": [{"content": {"parts": [None]}}]}):
            with self.subTest(payload=payload):
                with self.assertRaises(ModelError):
                    await self.call_model(lambda request: httpx.Response(200, json=payload))
        with self.assertRaises(ModelError):
            await self.call_model(lambda request: httpx.Response(200, text="not json"))

    async def test_timeout(self):
        def handler(request):
            raise httpx.ReadTimeout("timeout", request=request)
        with self.assertRaises(ModelError) as caught:
            await self.call_model(handler)
        self.assertEqual(caught.exception.status_code, 504)

    async def test_network_error(self):
        def handler(request):
            raise httpx.ConnectError("connection failed", request=request)
        with self.assertRaises(ModelError) as caught:
            await self.call_model(handler)
        self.assertEqual(caught.exception.status_code, 503)

    async def test_truncated_response(self):
        with self.assertRaises(ModelError):
            await self.call_model(lambda request: httpx.Response(200, json={"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "Partial"}]}}]}))


if __name__ == "__main__":
    unittest.main()
