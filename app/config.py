import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values


BASE_DIR = Path(__file__).resolve().parent.parent
KEY_PATTERN = r"[A-Za-z0-9_-]{8,255}"


@dataclass(frozen=True)
class Settings:
    access_key: str = field(repr=False)
    gemini_api_key: str = field(default="", repr=False)
    gemini_model: str = "gemini-3.1-flash-lite"
    database_path: Path = BASE_DIR / "data" / "chat.sqlite3"
    cookie_secure: bool = False
    session_ttl: int = 86400
    request_timeout: float = 60.0

    def __post_init__(self):
        if re.fullmatch(KEY_PATTERN, self.access_key) is None:
            raise ValueError("APP_ACCESS_KEY должен содержать 8–255 символов: A–Z, a–z, 0–9, _ и -")
        if re.fullmatch(r"[A-Za-z0-9._-]+", self.gemini_model) is None:
            raise ValueError("Некорректное имя GEMINI_MODEL")

    @classmethod
    def from_env(cls):
        values = {**dotenv_values(BASE_DIR / ".env"), **os.environ}
        secure = (values.get("COOKIE_SECURE") or "false").strip().lower()
        if secure not in {"true", "false"}:
            raise ValueError("COOKIE_SECURE должен быть true или false")
        database_path = Path(values.get("DATABASE_PATH") or "data/chat.sqlite3")
        if not database_path.is_absolute():
            database_path = BASE_DIR / database_path
        return cls(
            access_key=values.get("APP_ACCESS_KEY") or "",
            gemini_api_key=values.get("GEMINI_API_KEY") or "",
            gemini_model=values.get("GEMINI_MODEL") or "gemini-3.1-flash-lite",
            database_path=database_path,
            cookie_secure=secure == "true",
        )
