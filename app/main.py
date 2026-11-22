import asyncio
import logging
import secrets
import sqlite3
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.auth import COOKIE_NAME, LoginLimiter, create_session, require_session, token_hash
from app.config import BASE_DIR, Settings
from app.llm import GeminiClient, ModelError
from app.schemas import LoginRequest, MessageList, MessageRequest
from app.storage import Storage


logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.storage.initialize()
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            application.state.llm = GeminiClient(settings, client)
            yield

    app = FastAPI(title="Помощник по Python", lifespan=lifespan)
    app.state.settings = settings
    app.state.storage = Storage(settings.database_path)
    app.state.login_limiter = LoginLimiter()
    app.state.message_lock = asyncio.Lock()

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        origin = request.headers.get("origin")
        foreign_origin = origin is not None and origin != str(request.base_url).rstrip("/")
        cross_site = request.headers.get("sec-fetch-site") == "cross-site"
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and (foreign_origin or cross_site):
            response = JSONResponse(status_code=403, content={"detail": "Запрос отклонён."})
        else:
            response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path == "/":
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'self'"
            )
            response.headers["Cache-Control"] = "no-cache"
        elif request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        if request.url.path == "/api/login":
            message = "Ключ должен содержать 8–255 символов: латиницу, цифры, дефис или подчёркивание."
        else:
            message = "Введите сообщение от 1 до 500 символов. Одних пробелов недостаточно."
        return JSONResponse(status_code=422, content={"detail": message})

    @app.exception_handler(sqlite3.Error)
    async def database_error(request: Request, error: sqlite3.Error):
        logger.error("Database operation failed: %s", type(error).__name__)
        return JSONResponse(
            status_code=503,
            content={"detail": "Не удалось сохранить или загрузить данные. Повторите попытку."},
        )

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(BASE_DIR / "static" / "index.html")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/login")
    def login(data: LoginRequest, request: Request, response: Response):
        address = request.client.host if request.client else "unknown"
        app.state.login_limiter.check(address)
        if not secrets.compare_digest(data.key, settings.access_key):
            raise HTTPException(status_code=401, detail="Неверный ключ доступа.")
        token = create_session(request)
        app.state.login_limiter.clear(address)
        response.set_cookie(
            key=COOKIE_NAME,
            value=token,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
            max_age=settings.session_ttl,
            path="/",
        )
        return {"authenticated": True}

    @app.get("/api/session", dependencies=[Depends(require_session)])
    def session():
        return {"authenticated": True}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        token = request.cookies.get(COOKIE_NAME)
        if token:
            app.state.storage.delete_session(token_hash(token))
        response.delete_cookie(
            COOKIE_NAME, path="/", httponly=True,
            secure=settings.cookie_secure, samesite="strict",
        )
        return {"authenticated": False}

    @app.get("/api/messages", response_model=MessageList, dependencies=[Depends(require_session)])
    def history():
        return {"messages": app.state.storage.list_messages()}

    @app.post("/api/messages", response_model=MessageList, dependencies=[Depends(require_session)])
    async def send_message(data: MessageRequest):
        if app.state.message_lock.locked():
            raise HTTPException(status_code=409, detail="Дождитесь ответа на предыдущее сообщение.")
        async with app.state.message_lock:
            try:
                answer = await app.state.llm.answer(data.text)
            except ModelError as error:
                raise HTTPException(status_code=error.status_code, detail=str(error)) from error
            return {"messages": app.state.storage.save_exchange(data.text, answer)}

    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    return app
