import hashlib
import secrets
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request


COOKIE_NAME = "session_token"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def require_session(request: Request) -> str:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token or len(token) > 128:
        raise HTTPException(status_code=401, detail="Войдите, чтобы продолжить.")
    settings = request.app.state.settings
    if not request.app.state.storage.session_exists(token_hash(token), key_hash(settings.access_key)):
        raise HTTPException(status_code=401, detail="Сессия истекла. Войдите ещё раз.")
    return token


def create_session(request: Request) -> str:
    settings = request.app.state.settings
    storage = request.app.state.storage
    token = secrets.token_urlsafe(32)
    previous = request.cookies.get(COOKIE_NAME)
    if previous:
        storage.delete_session(token_hash(previous))
    storage.save_session(
        token_hash(token), key_hash(settings.access_key), time.time() + settings.session_ttl
    )
    return token


class LoginLimiter:
    def __init__(self, limit: int = 10, window: int = 300):
        self.limit = limit
        self.window = window
        self.attempts = defaultdict(deque)
        self.lock = Lock()

    def check(self, address: str):
        now = time.monotonic()
        with self.lock:
            stale = [ip for ip, attempts in self.attempts.items() if attempts[-1] <= now - self.window]
            for ip in stale:
                del self.attempts[ip]
            attempts = self.attempts[address]
            while attempts and attempts[0] <= now - self.window:
                attempts.popleft()
            if len(attempts) >= self.limit:
                raise HTTPException(
                    status_code=429,
                    detail="Слишком много попыток входа. Попробуйте через пять минут.",
                    headers={"Retry-After": str(self.window)},
                )
            attempts.append(now)

    def clear(self, address: str):
        with self.lock:
            self.attempts.pop(address, None)
