import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from idlix_downloader.web.app import create_app
from idlix_downloader.web.config import WebConfig
from idlix_downloader.web.errors import AppError
from idlix_downloader.web.schemas import InspectionRequest


@pytest.fixture
def client(tmp_path):
    app = create_app(WebConfig(tmp_path))

    @app.post("/api/test")
    def test_mutation(body: InspectionRequest):
        return {"accepted": True}

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        yield client


def test_health_and_origin_host_security(client):
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    assert client.get("/health/live", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/health/live", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/test", json={"url": "https://a.example/x.mp4"}).status_code == 403
    response = client.get("/api/session")
    token = response.json()["csrf_token"]
    headers = {"Origin": "http://127.0.0.1:8765", "X-CSRF-Token": token}
    assert (
        client.post(
            "/api/test", headers=headers, json={"url": "https://a.example/x.mp4"}
        ).status_code
        == 200
    )
    invalid = client.post(
        "/api/test", headers=headers, json={"url": "https://a.example", "cookies": "SECRET"}
    )
    assert invalid.status_code == 422
    assert "SECRET" not in invalid.text
    assert client.post("/api/test", headers=headers, content=b"a" * 16385).status_code == 413


def test_rate_limit_and_worker_health(client):
    for _ in range(120):
        client.get("/health/live")
    assert client.get("/health/live").status_code == 429


def test_startup_refuses_missing_ffmpeg_and_second_instance(tmp_path, monkeypatch):
    import idlix_downloader.web.app as web_app

    config = WebConfig(tmp_path)
    with TestClient(create_app(config), base_url="http://127.0.0.1:8765"):
        with pytest.raises(AppError, match="CONFLICT"):
            with TestClient(create_app(config)):
                pass
    monkeypatch.setattr(web_app, "ffmpeg_path", lambda: None)
    with pytest.raises(AppError, match="FFMPEG_MISSING"):
        with TestClient(create_app(config)):
            pass


def test_startup_refuses_unwritable_storage(tmp_path, monkeypatch):
    import idlix_downloader.web.app as web_app

    def denied(*args, **kwargs):
        raise PermissionError("fixture readonly")

    monkeypatch.setattr(web_app.tempfile, "TemporaryFile", denied)
    with pytest.raises(PermissionError):
        with TestClient(create_app(WebConfig(tmp_path))):
            pass


def test_readiness_detects_runner_failure(client):
    client.app.state.runner.failure = True
    assert client.get("/health/ready").status_code == 503


def test_download_input_rejects_missing_choice_and_operator_fields():
    from pydantic import ValidationError

    from idlix_downloader.web.schemas import DownloadRequest

    with pytest.raises(ValidationError):
        DownloadRequest(inspection_id="a" * 32)
    assert DownloadRequest(inspection_id="a" * 32, policy="best").choice_id is None
    with pytest.raises(ValidationError):
        DownloadRequest(inspection_id="a" * 32, policy="best", choice_id="b" * 24)
    with pytest.raises(ValidationError):
        DownloadRequest(inspection_id="a" * 32, policy="best", output="/tmp/arbitrary")
