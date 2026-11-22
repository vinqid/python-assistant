import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import KEY_PATTERN


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(strict=True, min_length=8, max_length=255)

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if re.fullmatch(KEY_PATTERN, value) is None:
            raise ValueError("Некорректный формат ключа")
        return value


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(strict=True, min_length=1, max_length=500)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Сообщение не должно быть пустым")
        return value


class Message(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    text: str
    created_at: datetime


class MessageList(BaseModel):
    messages: list[Message]
