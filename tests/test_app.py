import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.auth import COOKIE_NAME
from app.config import Settings
from app.llm import ModelError
from app.main import create_app


class AppTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = Settings(
            access_key="test-access-123",
            gemini_api_key="test-api-key",
            database_path=Path(self.directory.name) / "chat.sqlite3",
        )
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.app.state.llm.answer = AsyncMock(return_value="Используйте функцию len().")

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.directory.cleanup()

    def login(self):
        response = self.client.post("/api/login", json={"key": self.settings.access_key})
        self.assertEqual(response.status_code, 200)
        return response

    def test_private_endpoints_require_session(self):
        self.assertEqual(self.client.get("/api/session").status_code, 401)
        self.assertEqual(self.client.get("/api/messages").status_code, 401)
        self.assertEqual(self.client.post("/api/messages", json={"text": "test"}).status_code, 401)

    def test_key_validation_and_wrong_key(self):
        for key in ("a" * 7, "a" * 256, "ключ-доступа", "valid key", "valid-key\n", "", 12345678):
            with self.subTest(key=key):
                self.assertEqual(self.client.post("/api/login", json={"key": key}).status_code, 422)
        self.assertEqual(self.client.post("/api/login", json={"key": "wrong-key-123"}).status_code, 401)

    def test_key_length_boundaries(self):
        for length in (8, 255):
            with self.subTest(length=length):
                settings = Settings(access_key="a" * length, database_path=self.settings.database_path)
                with TestClient(create_app(settings)) as client:
                    self.assertEqual(client.post("/api/login", json={"key": "a" * length}).status_code, 200)

    def test_cookie_and_hashed_session(self):
        response = self.login()
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertIn("max-age=86400", cookie)
        token = self.client.cookies.get(COOKIE_NAME)
        self.assertNotIn(self.settings.access_key, cookie)
        with self.app.state.storage.connection() as connection:
            row = connection.execute("SELECT token_hash FROM sessions").fetchone()
        self.assertNotEqual(row["token_hash"], token)
        self.assertEqual(self.client.get("/api/session").status_code, 200)

    def test_logout_revokes_token(self):
        self.login()
        token = self.client.cookies.get(COOKIE_NAME)
        self.assertEqual(self.client.post("/api/logout").status_code, 200)
        self.client.cookies.set(COOKIE_NAME, token)
        self.assertEqual(self.client.get("/api/messages").status_code, 401)

    def test_expired_session(self):
        self.login()
        with self.app.state.storage.connection() as connection:
            connection.execute("UPDATE sessions SET expires_at = ?", (time.time() - 1,))
        self.assertEqual(self.client.get("/api/session").status_code, 401)

    def test_login_rotates_session(self):
        self.login()
        old_token = self.client.cookies.get(COOKIE_NAME)
        self.login()
        self.assertNotEqual(old_token, self.client.cookies.get(COOKIE_NAME))
        self.client.cookies.clear()
        self.client.cookies.set(COOKIE_NAME, old_token)
        self.assertEqual(self.client.get("/api/session").status_code, 401)

    def test_history_and_session_survive_restart(self):
        self.login()
        token = self.client.cookies.get(COOKIE_NAME)
        response = self.client.post("/api/messages", json={"text": "  Как узнать длину?  "})
        self.assertEqual(response.status_code, 200)
        messages = response.json()["messages"]
        self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
        self.assertEqual(messages[0]["text"], "Как узнать длину?")
        self.assertTrue(all(message["created_at"].endswith("Z") for message in messages))
        self.app.state.llm.answer.assert_awaited_once_with("Как узнать длину?")
        restarted = create_app(self.settings)
        with TestClient(restarted) as client:
            client.cookies.set(COOKIE_NAME, token)
            self.assertEqual(client.get("/api/session").status_code, 200)
            self.assertEqual(client.get("/api/messages").json()["messages"], messages)

    def test_changed_access_key_revokes_existing_sessions(self):
        self.login()
        token = self.client.cookies.get(COOKIE_NAME)
        settings = Settings(access_key="new-access-key", database_path=self.settings.database_path)
        with TestClient(create_app(settings)) as client:
            client.cookies.set(COOKIE_NAME, token)
            self.assertEqual(client.get("/api/session").status_code, 401)

    def test_message_boundaries_and_whitespace(self):
        self.login()
        for text in ("", "   ", "\t\n", "a" * 501, " " + "a" * 500, 123, None):
            with self.subTest(text=text):
                response = self.client.post("/api/messages", json={"text": text})
                self.assertEqual(response.status_code, 422)
        self.app.state.llm.answer.assert_not_awaited()
        for text in ("a", "a" * 500, "🐍" * 500):
            with self.subTest(length=len(text)):
                self.assertEqual(self.client.post("/api/messages", json={"text": text}).status_code, 200)

    def test_model_failure_does_not_save_partial_exchange(self):
        self.login()
        self.app.state.llm.answer = AsyncMock(side_effect=ModelError("Нет ответа", 504))
        response = self.client.post("/api/messages", json={"text": "Вопрос"})
        self.assertEqual(response.status_code, 504)
        self.assertEqual(self.client.get("/api/messages").json()["messages"], [])
        self.app.state.llm.answer = AsyncMock(return_value="Ответ")
        self.assertEqual(self.client.post("/api/messages", json={"text": "Вопрос"}).status_code, 200)

    def test_exchange_is_atomic(self):
        with self.app.state.storage.connection() as connection:
            connection.execute(
                "CREATE TRIGGER fail_answer BEFORE INSERT ON messages "
                "WHEN NEW.role = 'assistant' BEGIN SELECT RAISE(ABORT, 'test'); END;"
            )
        self.login()
        self.assertEqual(self.client.post("/api/messages", json={"text": "Вопрос"}).status_code, 503)
        self.assertEqual(self.app.state.storage.list_messages(), [])

    def test_duplicate_request_while_busy(self):
        self.login()
        self.app.state.message_lock = asyncio.Lock()
        self.client.portal.call(self.app.state.message_lock.acquire)
        try:
            response = self.client.post("/api/messages", json={"text": "Вопрос"})
            self.assertEqual(response.status_code, 409)
            self.app.state.llm.answer.assert_not_awaited()
        finally:
            self.client.portal.call(self.app.state.message_lock.release)

    def test_login_rate_limit(self):
        for _ in range(10):
            self.assertEqual(self.client.post("/api/login", json={"key": "wrong-key-123"}).status_code, 401)
        response = self.client.post("/api/login", json={"key": self.settings.access_key})
        self.assertEqual(response.status_code, 429)
        self.assertIn("retry-after", response.headers)

    def test_cross_origin_requests_are_rejected(self):
        self.assertEqual(
            self.client.post("/api/login", json={"key": self.settings.access_key}, headers={"Origin": "https://other.example"}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post("/api/logout", headers={"Sec-Fetch-Site": "cross-site"}).status_code,
            403,
        )

    def test_static_files_and_security_headers(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Вход по ключу", response.text)
        self.assertIn("script-src 'self'", response.headers["content-security-policy"])
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        self.assertEqual(self.client.get("/static/styles.css").status_code, 200)
        self.assertEqual(self.client.get("/.env").status_code, 404)
        self.assertEqual(self.client.get("/api/session").headers["cache-control"], "no-store")

    def test_sql_and_html_are_saved_as_text(self):
        self.login()
        text = "<script>alert(1)</script>'; DROP TABLE messages; --"
        self.assertEqual(self.client.post("/api/messages", json={"text": text}).status_code, 200)
        self.assertEqual(self.client.get("/api/messages").json()["messages"][0]["text"], text)


if __name__ == "__main__":
    unittest.main()
