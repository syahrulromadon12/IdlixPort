"""Local API lifecycle with guarded job and file operations."""

import asyncio
import hmac
import secrets
import tempfile
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..downloader import ffmpeg_path
from .errors import AppError
from .repository import Repository
from .routes import install_routes
from .runner import Runner
from .service import Service
from .sessions import Sessions


class LocalSecurity:
    def __init__(self, app, config, csrf):
        self.app, self.config, self.csrf = app, config, csrf
        self.requests = defaultdict(deque)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        origin = headers.get(b"origin", b"").decode(errors="replace")
        mutation = scope["method"] not in ("GET", "HEAD", "OPTIONS")
        allowed = origin in self.config.origins
        token = headers.get(b"x-csrf-token", b"")
        if (origin and not allowed) or (
            mutation and (not allowed or not hmac.compare_digest(token, self.csrf.encode()))
        ):
            return await JSONResponse(AppError("NETWORK_BLOCKED").public(), status_code=403)(
                scope, receive, send
            )
        now = time.monotonic()
        peer = (scope.get("client") or ("local",))[0]
        recent = self.requests[peer]
        while recent and recent[0] < now - 60:
            recent.popleft()
        if len(recent) >= 120:
            return await JSONResponse(AppError("QUEUE_FULL").public(), status_code=429)(
                scope, receive, send
            )
        recent.append(now)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            limit = 196608 if scope.get("path") == "/api/batches" else 16384
            if len(body) > limit:
                return await JSONResponse({"code": "BODY_TOO_LARGE"}, status_code=413)(
                    scope, receive, send
                )
            if not message.get("more_body"):
                break
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app(config):
    csrf = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        config.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=config.data_dir) as handle:
            handle.write(b"readiness")
            handle.flush()
        if not ffmpeg_path():
            raise AppError("FFMPEG_MISSING")
        repository = Repository(config.data_dir, config.queue_limit)
        runner = Runner(repository, config)
        app.state.repository, app.state.runner = repository, runner
        app.state.artifacts = runner.artifacts
        sessions = Sessions(repository, config, runner)
        runner.sessions = app.state.sessions = sessions
        app.state.service = Service(
            repository, config, runner.destinations, app.state.artifacts, sessions
        )
        runner.start()
        sessions.start()
        try:
            yield
        finally:
            try:
                await asyncio.to_thread(sessions.stop)
            finally:
                await asyncio.to_thread(runner.stop)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(LocalSecurity, config=config, csrf=csrf)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"], www_redirect=False
    )

    @app.exception_handler(RequestValidationError)
    async def invalid_input(_request, _error):
        return JSONResponse(
            {"code": "INVALID_INPUT", "message": "Input tidak valid."}, status_code=422
        )

    @app.exception_handler(AppError)
    async def app_error(_request, error):
        status = {
            "QUEUE_FULL": 429,
            "NETWORK_BLOCKED": 403,
            "CONSENT_REQUIRED": 403,
            "SESSION_EXPIRED": 409,
            "BROWSER_UNAVAILABLE": 409,
            "INVALID_INPUT": 422,
            "INSPECTION_EXPIRED": 409,
            "QUALITY_CHANGED": 409,
            "UNKNOWN_RESOLUTION": 422,
            "ARTIFACT_UNAVAILABLE": 404,
            "CONFLICT": 409,
            "RETRY_LIMIT": 409,
            "DISK_FULL": 507,
            "OPEN_UNAVAILABLE": 409,
            "FOLDER_UNAVAILABLE": 409,
            "OUTPUT_UNWRITABLE": 503,
        }.get(error.code, 503)
        return JSONResponse(error.public(), status_code=status)

    @app.exception_handler(KeyError)
    async def missing(_request, _error):
        return JSONResponse(
            {"code": "NOT_FOUND", "message": "Data tidak ditemukan."}, status_code=404
        )

    @app.exception_handler(Exception)
    async def internal_error(_request, _error):
        return JSONResponse(AppError("WORKER_FAILED").public(), status_code=500)

    @app.get("/health/live")
    async def live():
        return {"status": "alive"}

    @app.get("/health/ready")
    async def ready(request: Request):
        runner = request.app.state.runner
        try:
            with request.app.state.repository.connection() as db:
                db.execute("SELECT 1").fetchone()
            healthy = runner.healthy and bool(ffmpeg_path())
        except Exception:
            healthy = False
        return JSONResponse(
            {"status": "ready" if healthy else "unavailable"}, status_code=200 if healthy else 503
        )

    @app.get("/api/session")
    async def session():
        return JSONResponse({"csrf_token": csrf}, headers={"Cache-Control": "no-store"})

    install_routes(app)
    return app
