"""P2 API/worker flows use synthetic media and a transport adapter only in tests."""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time

import pytest
from test_integration import media_server as media_server

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from idlix_downloader.web.app import create_app
from idlix_downloader.web.config import WebConfig
from idlix_downloader.web.runner import Runner


def fixture_worker(port):
    # No production switch for allowing private IPs. Only this test child routes
    # the validated public fixture address back to the loopback media server.
    return f"""
import socket,sys,time
lookup=socket.getaddrinfo
original_socket=socket.socket
def resolve(host,port,*args,**kwargs):
    if host=='source.example':
        return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('1.1.1.1',port))]
    return lookup(host,port,*args,**kwargs)
class FixtureSocket(original_socket):
    def connect(self,target):
        if target[0]=='1.1.1.1':target=('127.0.0.1',{port})
        return super().connect(target)
socket.getaddrinfo=resolve
socket.socket=FixtureSocket
from idlix_downloader.web import worker
original_run=worker.run_job
def run(config,events):
    if 'slow.mp4' in config['payload']['url']:
        time.sleep(5)
    if config['kind']=='download':
        events.phase('extracting')
        time.sleep(1.2)
    return original_run(config,events)
worker.run_job=run
sys.exit(worker.main())
"""


@pytest.fixture
def flow_app(tmp_path, media_server, monkeypatch):
    base, root, requests, binary = media_server
    port = int(base.rsplit(":", 1)[1])
    helper = fixture_worker(port)
    monkeypatch.setattr(Runner, "command", lambda self, job: [sys.executable, "-c", helper])
    data = tmp_path / "data"
    folder = tmp_path / "chosen-folder"
    app = create_app(WebConfig(data, allowed_hosts=("source.example",), download_dirs=(folder,)))
    opened = []
    monkeypatch.setattr("idlix_downloader.web.files.open_local", lambda path: opened.append(path))
    return app, folder, root, opened


@pytest.fixture
def flow_client(flow_app):
    app, folder, root, opened = flow_app
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.headers.update(
            {
                "Origin": "http://127.0.0.1:8765",
                "X-CSRF-Token": client.get("/api/session").json()["csrf_token"],
            }
        )
        yield client, app, folder, root, opened


def wait_job(client, job_id):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["state"] in ("completed", "failed", "interrupted", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError("Job did not terminate")


def inspect(client, url="http://source.example/master.m3u8?secret=SOURCE_SECRET"):
    response = client.post("/api/inspections", json={"url": url})
    assert response.status_code == 202
    job = wait_job(client, response.json()["id"])
    assert job["state"] == "completed", job.get("error")
    return job


def test_real_api_worker_download_and_local_files(flow_client):
    client, app, folder, _, opened = flow_client
    job = inspect(client)
    assert "SOURCE_SECRET" not in json.dumps(job)
    assert job["expires_at"] > time.time()
    choice = next(item for item in job["metadata"]["choices"] if item["quality_class"] == 1080)
    folders = client.get("/api/folders").json()["folders"]
    selected_folder = next(item["id"] for item in folders if item["label"] == "chosen-folder")
    body = {
        "inspection_id": job["id"],
        "choice_id": choice["choice_id"],
        "folder_id": selected_folder,
    }
    response = client.post("/api/jobs", json=body, headers={"Idempotency-Key": "same-click"})
    assert response.status_code == 202
    download_id = response.json()["id"]
    assert (
        client.post("/api/jobs", json=body, headers={"Idempotency-Key": "same-click"}).json()["id"]
        == download_id
    )
    result = wait_job(client, download_id)
    assert result["state"] == "completed", result.get("error")
    assert result["effective_quality"]["quality_class"] == 1080
    artifact = result["files"][0]
    actual = folder / download_id / artifact["name"]
    assert actual.is_file()
    assert "SOURCE_SECRET" not in json.dumps(client.get("/api/jobs").json())
    file = client.get(f"/api/artifacts/{artifact['id']}/file")
    assert file.status_code == 200
    assert hashlib.sha256(file.content).digest() == hashlib.sha256(actual.read_bytes()).digest()
    partial = client.get(f"/api/artifacts/{artifact['id']}/file", headers={"Range": "bytes=1-9"})
    assert partial.status_code == 206 and partial.content == file.content[1:10]
    assert (
        client.get(
            f"/api/artifacts/{artifact['id']}/file", headers={"Range": "bytes=99999999-"}
        ).status_code
        == 416
    )
    assert client.post(f"/api/artifacts/{artifact['id']}/open").status_code == 200
    assert client.post(f"/api/jobs/{download_id}/open-folder").status_code == 200
    assert opened == [actual, actual.parent]
    lease, _ = app.state.artifacts.acquire(artifact["id"])
    try:
        assert client.delete(f"/api/artifacts/{artifact['id']}").status_code == 409
    finally:
        lease.close()
    assert client.delete(f"/api/artifacts/{artifact['id']}").status_code == 204
    assert not actual.exists()
    assert client.get(f"/api/artifacts/{artifact['id']}/file").status_code == 404


def test_expiry_idempotency_and_changed_choice(flow_client):
    client, app, *_ = flow_client
    job = inspect(client)
    choice = next(item for item in job["metadata"]["choices"] if item["quality_class"] == 720)
    body = {"inspection_id": job["id"], "choice_id": choice["choice_id"]}
    first = client.post("/api/jobs", json=body, headers={"Idempotency-Key": "expiry"})
    assert first.status_code == 202
    with app.state.repository.connection() as db:
        db.execute("UPDATE inspections SET expires=0 WHERE job_id=?", (job["id"],))
    assert client.get(f"/api/inspections/{job['id']}").json()["expired"]
    assert (
        client.post("/api/jobs", json=body, headers={"Idempotency-Key": "expiry"}).json()
        == first.json()
    )
    assert (
        client.post("/api/jobs", json=body, headers={"Idempotency-Key": "new"}).status_code == 409
    )
    assert (
        client.post(
            "/api/jobs",
            json={**body, "policy": "best", "choice_id": None},
            headers={"Idempotency-Key": "expiry"},
        ).status_code
        == 409
    )
    assert wait_job(client, first.json()["id"])["state"] == "completed"
    refreshed = client.post(f"/api/inspections/{job['id']}/refresh")
    assert refreshed.status_code == 202
    assert refreshed.json()["id"] != job["id"]


def test_input_errors_and_failure_are_safe(flow_client):
    client, app, *_ = flow_client
    assert client.post("/api/inspections", json={"url": "file:///private"}).status_code == 422
    assert (
        client.post("/api/inspections", json={"url": "http://127.0.0.1/x.mp4"}).status_code == 403
    )
    missing = client.post(
        "/api/inspections", json={"url": "http://source.example/missing.m3u8?secret=SOURCE_SECRET"}
    )
    failed = wait_job(client, missing.json()["id"])
    assert failed["state"] == "failed"
    assert "SOURCE_SECRET" not in json.dumps(failed)
    assert client.get("/api/inspections/" + "a" * 32).status_code == 404
    assert (
        client.post("/api/jobs", json={"inspection_id": "a" * 32, "policy": "best"}).status_code
        == 404
    )
    assert (
        client.post(
            "/api/jobs", json={"inspection_id": "a" * 32, "policy": "best", "output": "/private"}
        ).status_code
        == 422
    )


def test_probe_timeout_is_terminal_and_not_completed(flow_client, monkeypatch):
    client, *_ = flow_client
    monkeypatch.setattr("idlix_downloader.web.runner.INSPECTION_TIMEOUT", 0.2)
    response = client.post("/api/inspections", json={"url": "http://source.example/slow.mp4"})
    assert response.status_code == 202
    assert wait_job(client, response.json()["id"])["state"] == "failed"


def test_disk_full_and_unknown_folder_rejected_before_download(flow_client, monkeypatch):
    client, app, *_ = flow_client
    job = inspect(client)
    body = {"inspection_id": job["id"], "policy": "best"}
    assert client.post("/api/jobs", json={**body, "folder_id": "f" * 16}).status_code == 409
    usage = shutil.disk_usage(app.state.repository.root)
    monkeypatch.setattr(
        "idlix_downloader.web.files.shutil.disk_usage", lambda path: usage._replace(free=0)
    )
    response = client.post("/api/jobs", json=body)
    assert response.status_code == 507 and response.json()["code"] == "DISK_FULL"


def test_explicit_choice_that_is_not_from_inspection_is_rejected(flow_client):
    client, *_ = flow_client
    job = inspect(client)
    response = client.post("/api/jobs", json={"inspection_id": job["id"], "choice_id": "f" * 24})
    assert response.status_code == 409
    assert response.json()["code"] == "QUALITY_CHANGED"


@pytest.fixture
def browser_server(tmp_path, media_server, monkeypatch):
    import socket
    import threading

    import uvicorn

    _, _, _, _ = media_server
    helper = fixture_worker(int(media_server[0].rsplit(":", 1)[1]))
    monkeypatch.setattr(Runner, "command", lambda self, job: [sys.executable, "-c", helper])
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    app = create_app(
        WebConfig(
            tmp_path / "data",
            port=port,
            allowed_hosts=("source.example",),
            download_dirs=(tmp_path / "chosen-folder",),
        )
    )
    opened = []
    monkeypatch.setattr("idlix_downloader.web.files.open_local", lambda path: opened.append(path))
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    try:
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline
            time.sleep(0.05)
        yield f"http://127.0.0.1:{port}", opened
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        listener.close()
        assert not thread.is_alive()


@pytest.mark.skipif(
    os.environ.get("IDLIXPORT_BROWSER_TESTS") != "1",
    reason="Requires IDLIXPORT_BROWSER_TESTS=1, Node 22+ and Chrome",
)
@pytest.mark.parametrize("viewport", ["desktop", "mobile"])
def test_browser_flow_when_enabled(browser_server, viewport):
    import subprocess
    from pathlib import Path

    base, opened = browser_server
    result = subprocess.run(
        [
            "node",
            str(Path(__file__).parent / "browser/web-flow.mjs"),
            base,
            viewport,
            str(Path(tempfile.gettempdir()) / "idlixport-browser-artifacts"),
        ],
        capture_output=True,
        text=True,
        timeout=100,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"flow":"passed"' in result.stdout
    assert len(opened) == 2
    assert opened[1] == opened[0].parent
    assert not opened[0].exists()  # Browser explicitly confirmed deleting this result.
    assert '"confirmed_delete":"passed"' in result.stdout
