"""Auth-context API and real media workers; no real browser or source account."""

import json
import sys
import time
from unittest.mock import Mock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient
from test_integration import media_server as media_server
from test_web_flow import fixture_worker, wait_job
from test_web_sessions import COOKIE_SECRET, URL, probe_command, wait_state

from idlix_downloader.web.app import create_app
from idlix_downloader.web.browser_profiles import Profile
from idlix_downloader.web.config import WebConfig
from idlix_downloader.web.runner import Runner
from idlix_downloader.web.sessions import Sessions


def authenticated_worker(port, account_state):
    helper = fixture_worker(port)
    injected = f"""
from pathlib import Path
from idlix_downloader.web.inspection import WebIdlixIE
def authenticated(self,url):
    present=any(c.name=='auth' and c.value=={COOKIE_SECRET!r} for c in self._downloader.cookiejar)
    account=Path({str(account_state)!r}).read_text()
    member=present and account!='guest'
    if hasattr(self._downloader,'web_session_evidence'):
        self._downloader.web_session_evidence['account_verified']=member
    maximum=1080 if member and account!='limited' else 720
    source='revoking-master.m3u8' if account=='transfer-expired' else 'master.m3u8'
    formats,subtitles=self._extract_m3u8_formats_and_subtitles(
        'http://source.example/'+source,'fixture',fatal=True)
    formats=[f for f in formats if not f.get('height') or f['height']<=maximum]
    return dict(id='fixture',title='Account fixture',formats=formats,
        session_max_height=maximum,session_viewer_tier='member' if member else 'guest')
WebIdlixIE._real_extract=authenticated
"""
    return helper.replace("sys.exit(worker.main())", injected + "\nsys.exit(worker.main())")


def make_session_app(tmp_path, media_server, monkeypatch, port=8765, browser="chrome"):
    profile_path = tmp_path / "profile/Default"
    profile_path.mkdir(parents=True)
    cookie = profile_path / "Cookies"
    cookie.write_text("member")
    account = tmp_path / "account-state"
    account.write_text("member")
    profile = Profile(browser, profile_path, cookie, application="Google Chrome")
    monkeypatch.setattr(
        "idlix_downloader.web.sessions.discover_profiles", lambda: {profile.id: profile}
    )
    monkeypatch.setattr(Profile, "login", Mock(return_value=browser == "chrome"))
    monkeypatch.setattr(Sessions, "command", lambda self: probe_command())
    monkeypatch.setattr("idlix_downloader.web.sessions.MIN_INTERVAL", 0.1)
    helper = authenticated_worker(int(media_server[0].rsplit(":", 1)[1]), account)
    monkeypatch.setattr(Runner, "command", lambda self, job: [sys.executable, "-c", helper])
    app = create_app(
        WebConfig(tmp_path / "data", port=port, allowed_hosts=("source.example", "z3.idlixku.com"))
    )
    return app, profile, account


@pytest.fixture
def authenticated_client(tmp_path, media_server, monkeypatch):
    app, profile, account = make_session_app(tmp_path, media_server, monkeypatch)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.headers.update(
            {
                "Origin": "http://127.0.0.1:8765",
                "X-CSRF-Token": client.get("/api/session").json()["csrf_token"],
            }
        )
        yield client, app, profile, account


def inspect_source(client):
    response = client.post("/api/inspections", json={"url": URL})
    assert response.status_code == 202
    job = wait_job(client, response.json()["id"])
    assert job["state"] == "completed", job["error"]
    return job


def connect(client, app, profile):
    response = client.post(
        "/api/browser-session/connect", json={"profile_id": profile.id, "url": URL, "consent": True}
    )
    assert response.status_code == 200
    return wait_state(app.state.sessions, {"connected"})


def test_guest_download_completes_at_720_without_browser_session(authenticated_client):
    client, app, _, _ = authenticated_client
    guest = inspect_source(client)
    response = client.post("/api/jobs", json={"inspection_id": guest["id"], "policy": "best"})
    assert response.status_code == 202
    result = wait_job(client, response.json()["id"])
    assert result["state"] == "completed", result
    assert result["effective_quality"]["quality_class"] == 720
    assert any(file["available"] for file in result["files"])
    assert app.state.sessions.status()["state"] == "disconnected"


def test_guest_account_reinspection_entitlement_and_private_cookie_ipc(authenticated_client):
    client, app, profile, account = authenticated_client
    guest = inspect_source(client)
    assert max(c["quality_class"] for c in guest["metadata"]["choices"]) == 720
    connect(client, app, profile)
    assert (
        client.post("/api/jobs", json={"inspection_id": guest["id"], "policy": "best"}).status_code
        == 409
    )
    member = inspect_source(client)
    assert max(c["quality_class"] for c in member["metadata"]["choices"]) == 1080
    response = client.post("/api/jobs", json={"inspection_id": member["id"], "policy": "max_1080"})
    completed = wait_job(client, response.json()["id"])
    assert completed["state"] == "completed"
    assert completed["effective_quality"]["quality_class"] == 1080
    assert COOKIE_SECRET not in json.dumps(client.get("/api/jobs").json())
    with app.state.repository.connection() as db:
        stored = str(list(db.execute("SELECT payload,result FROM jobs")))
    assert COOKIE_SECRET not in stored
    account.write_text("limited")
    limited = inspect_source(client)
    assert max(c["quality_class"] for c in limited["metadata"]["choices"]) == 720
    assert limited["metadata"]["session_max_height"] == 720


def test_disconnect_stops_running_and_queued_context_jobs(authenticated_client):
    client, app, profile, _ = authenticated_client
    connect(client, app, profile)
    inspection = inspect_source(client)
    ids = [
        client.post(
            "/api/jobs", json={"inspection_id": inspection["id"], "policy": "max_1080"}
        ).json()["id"]
        for _ in range(2)
    ]
    deadline = time.monotonic() + 3
    while not app.state.runner.active and time.monotonic() < deadline:
        time.sleep(0.01)
    reference = app.state.sessions.reference
    assert client.delete("/api/browser-session").status_code == 200
    assert all(app.state.repository.get(ident)["state"] == "cancelled" for ident in ids)
    assert not app.state.sessions.cookies and not app.state.runner.active
    assert client.post(f"/api/jobs/{ids[0]}/retry").status_code == 409
    snapshot = client.get(f"/api/jobs/{ids[0]}").json()
    assert snapshot["needs_reinspection"] and not snapshot["can_retry"]
    assert snapshot["inspection_id"] == inspection["id"]
    refreshed = client.post(f"/api/inspections/{snapshot['inspection_id']}/refresh")
    assert refreshed.status_code == 202
    assert reference is not None


def test_revoked_account_at_start_fails_and_cancels_related_queue(authenticated_client):
    client, app, profile, account = authenticated_client
    connect(client, app, profile)
    inspection = inspect_source(client)
    account.write_text("guest")
    ids = [
        client.post(
            "/api/jobs", json={"inspection_id": inspection["id"], "policy": "max_1080"}
        ).json()["id"]
        for _ in range(2)
    ]
    failed = wait_job(client, ids[0])
    assert failed["state"] == "failed" and failed["error"]["code"] == "SESSION_EXPIRED"
    wait_state(app.state.sessions, {"expired"})
    assert app.state.repository.get(ids[1])["state"] == "cancelled"
    assert app.state.sessions.cookies == []


def test_revoked_session_during_fragment_transfer_cannot_complete(
    authenticated_client, media_server
):
    client, app, profile, account = authenticated_client
    connect(client, app, profile)
    inspection = inspect_source(client)
    root = media_server[1]
    (root / "revoking-master.m3u8").write_text(
        (root / "master.m3u8").read_text().replace("1080.m3u8", "revoking-video.m3u8")
    )
    (root / "revoking-video.m3u8").write_text(
        "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1\n#EXT-X-MEDIA-SEQUENCE:0\n"
        "#EXTINF:1,\n10800.ts\n#EXTINF:1,\nexpired.ts\n#EXT-X-ENDLIST\n"
    )
    account.write_text("transfer-expired")
    response = client.post(
        "/api/jobs", json={"inspection_id": inspection["id"], "policy": "max_1080"}
    )
    assert response.status_code == 202
    result = wait_job(client, response.json()["id"])
    assert result["state"] == "failed", result
    assert result["error"]["code"] == "SESSION_EXPIRED"
    assert result["phase"] in {"downloading", "merging"}
    assert not result["files"]
    wait_state(app.state.sessions, {"expired"})


def test_batch_binds_only_matching_source_and_disconnect_revokes_children(authenticated_client):
    client, app, profile, _ = authenticated_client
    connect(client, app, profile)
    app.state.runner.stop()
    response = client.post(
        "/api/batches",
        json={"urls": [URL, "http://source.example/master.m3u8"], "subtitle_mode": "off"},
    )
    assert response.status_code == 202, response.text
    jobs = app.state.repository.batch(response.json()["id"])
    first, second = [app.state.repository.get(ident) for ident in jobs]
    assert first["payload"]["session_ref"] == app.state.sessions.reference
    assert second["payload"]["session_ref"] is None
    assert client.delete("/api/browser-session").status_code == 200
    assert app.state.repository.get(jobs[0])["state"] == "cancelled"
    assert app.state.repository.get(jobs[1])["state"] == "queued"
