"""Public local routes; jobs expose projections, never their private payload."""

from pathlib import Path
from typing import Annotated

from fastapi import Header, Query, Request, Response
from fastapi import Path as ApiPath
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from .schemas import ConnectRequest, DownloadRequest, InspectionRequest
from .streaming import file_response

ID = Annotated[str, ApiPath(pattern=r"^[a-f0-9]{32}$")]
Key = Annotated[str | None, Header(alias="Idempotency-Key")]
STATIC = Path(__file__).parent / "static"


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


def install_routes(app):
    app.mount("/static", StaticFiles(directory=STATIC, follow_symlink=False), name="static")

    @app.get("/")
    def home():
        return FileResponse(
            STATIC / "index.html",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
                ),
            },
        )

    @app.get("/api/browser-session")
    def browser_session(request: Request):
        return request.app.state.sessions.status()

    @app.post("/api/browser-session/connect")
    def connect_browser(body: ConnectRequest, request: Request):
        return request.app.state.sessions.connect(body.profile_id, body.url, body.consent)

    @app.post("/api/browser-session/probe")
    def probe_browser(request: Request, body: EmptyRequest | None = None):
        return request.app.state.sessions.focus()

    @app.post("/api/browser-session/retry")
    def retry_browser(request: Request, body: EmptyRequest | None = None):
        return request.app.state.sessions.retry()

    @app.delete("/api/browser-session")
    def disconnect_browser(request: Request, body: EmptyRequest | None = None):
        return request.app.state.sessions.disconnect()

    @app.post("/api/inspections", status_code=202)
    def inspect(body: InspectionRequest, request: Request, idempotency_key: Key = None):
        job_id = request.app.state.service.inspect(body.url, idempotency_key)
        return {"id": job_id}

    @app.get("/api/inspections/{job_id}")
    def inspection(job_id: ID, request: Request):
        return request.app.state.service.snapshot(job_id, inspection_only=True)

    @app.post("/api/inspections/{job_id}/refresh", status_code=202)
    def refresh(
        job_id: ID, request: Request, body: EmptyRequest | None = None, idempotency_key: Key = None
    ):
        service = request.app.state.service
        job = service.repository.get(job_id)
        if job["kind"] != "inspect":
            raise KeyError(job_id)
        return {"id": service.inspect(job["payload"]["url"], idempotency_key)}

    @app.post("/api/jobs", status_code=202)
    def download(body: DownloadRequest, request: Request, idempotency_key: Key = None):
        return {"id": request.app.state.service.download(body, idempotency_key)}

    @app.get("/api/jobs")
    def jobs(request: Request):
        service = request.app.state.service
        ids = list(dict.fromkeys(service.repository.active_ids() + service.repository.recent()))
        return {"jobs": [service.snapshot(job_id) for job_id in ids]}

    @app.get("/api/history")
    def history(
        request: Request,
        offset: int = Query(0, ge=0, le=1000000),
        limit: int = Query(20, ge=1, le=50),
    ):
        service = request.app.state.service
        ids, total = service.repository.page(offset, limit)
        return {
            "jobs": [service.snapshot(ident) for ident in ids],
            "total": total,
            "offset": offset,
            "limit": limit,
            "next_offset": offset + limit if offset + limit < total else None,
        }

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: ID, request: Request, body: EmptyRequest | None = None):
        request.app.state.repository.cancel(job_id)
        return request.app.state.service.snapshot(job_id)

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    def retry(
        job_id: ID, request: Request, body: EmptyRequest | None = None, idempotency_key: Key = None
    ):
        return {"id": request.app.state.service.retry(job_id, idempotency_key)}

    @app.delete("/api/jobs/{job_id}/files", status_code=204)
    def delete_results(job_id: ID, request: Request, body: EmptyRequest | None = None):
        request.app.state.artifacts.delete_results(job_id)
        return Response(status_code=204)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: ID, request: Request):
        return request.app.state.service.snapshot(job_id)

    @app.get("/api/folders")
    def folders(request: Request):
        return {"folders": request.app.state.service.destinations.listing()}

    @app.post("/api/jobs/{job_id}/open-folder")
    def open_folder(job_id: ID, request: Request, body: EmptyRequest | None = None):
        request.app.state.artifacts.open_folder(job_id)
        return {"opened": True}

    @app.post("/api/artifacts/{artifact_id}/open")
    def open_file(artifact_id: ID, request: Request, body: EmptyRequest | None = None):
        request.app.state.artifacts.open(artifact_id)
        return {"opened": True}

    @app.get("/api/artifacts/{artifact_id}/file")
    def stream_file(artifact_id: ID, request: Request):
        return file_response(request.app.state.artifacts, artifact_id, request.headers.get("range"))

    @app.delete("/api/artifacts/{artifact_id}", status_code=204)
    def delete_file(artifact_id: ID, request: Request, body: EmptyRequest | None = None):
        request.app.state.artifacts.delete(artifact_id)
        return Response(status_code=204)
