import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class Storage:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    key_hash TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
                """
            )
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (time.time(),))

    def list_messages(self) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT id, role, text, created_at FROM messages ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def save_exchange(self, text: str, answer: str) -> list[dict]:
        messages = []
        with self.connection() as connection:
            for role, content in (("user", text), ("assistant", answer)):
                created_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                cursor = connection.execute(
                    "INSERT INTO messages (role, text, created_at) VALUES (?, ?, ?)",
                    (role, content, created_at),
                )
                messages.append(
                    {"id": cursor.lastrowid, "role": role, "text": content, "created_at": created_at}
                )
        return messages

    def save_session(self, token_hash: str, key_hash: str, expires_at: float):
        with self.connection() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (time.time(),))
            connection.execute(
                "INSERT INTO sessions (token_hash, key_hash, expires_at) VALUES (?, ?, ?)",
                (token_hash, key_hash, expires_at),
            )

    def session_exists(self, token_hash: str, key_hash: str) -> bool:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM sessions WHERE token_hash = ? AND key_hash = ? AND expires_at > ?",
                (token_hash, key_hash, time.time()),
            ).fetchone()
        return row is not None

    def delete_session(self, token_hash: str):
        with self.connection() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
