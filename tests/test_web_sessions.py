"""P4 uses isolated fake browser databases and private probe subprocesses."""

import json
import sys
import time
from unittest.mock import Mock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from idlix_downloader.web.app import create_app
from idlix_downloader.web.browser_profiles import Profile
from idlix_downloader.web.config import WebConfig
from idlix_downloader.web.errors import AppError
from idlix_downloader.web.repository import Repository
from idlix_downloader.web.runner import Runner
from idlix_downloader.web.sessions import Sessions

URL = "https://z3.idlixku.com/movie/fixture"
COOKIE_SECRET = "SYNTHETIC_COOKIE_SECRET"


def probe_command():
    code = r"""
import json,sys,time
from pathlib import Path
config=json.loads(sys.stdin.readline())
values=config.get('cookies',[])
if config.get('browser'):
    supplied=Path(config['profile'])
    folder=supplied.parent if supplied.is_file() else supplied
    mode=(supplied if supplied.is_file() else folder/'Cookies').read_text()
    with (folder/'reads').open('a') as out:out.write('read\n')
    if mode=='slow':time.sleep(60)
    if mode=='permission':
        print(json.dumps({'code':'COOKIE_PERMISSION'}));sys.exit(0)
    values=[dict(name='auth',value='SYNTHETIC_COOKIE_SECRET' if mode=='member' else mode,
        domain='.idlixku.com',domain_specified=True,domain_initial_dot=True,
        path='/',path_specified=True,secure=True,expires=None,discard=True)]
mode=values[0]['value'] if values else 'guest'
member=mode=='SYNTHETIC_COOKIE_SECRET'
print(json.dumps(dict(cookies=values,account_verified=member or mode=='verification',
 media_ready=member,max_height=1080 if member else 720,
 code=(None if member else 'VERIFICATION_REQUIRED'
       if mode=='verification' else 'SESSION_NOT_VERIFIED'))))
"""
    return [sys.executable, "-c", code]


@pytest.fixture
def session_environment(tmp_path, monkeypatch):
    profile_path = tmp_path / "browser/Default"
    profile_path.mkdir(parents=True)
    cookie = profile_path / "Cookies"
    cookie.write_text("guest")
    profile = Profile("chrome", profile_path, cookie, application="Google Chrome")
    monkeypatch.setattr(
        "idlix_downloader.web.sessions.discover_profiles", lambda: {profile.id: profile}
    )
    monkeypatch.setattr(Profile, "login", Mock(return_value=True))
    monkeypatch.setattr(Sessions, "command", lambda self: probe_command())
    monkeypatch.setattr("idlix_downloader.web.sessions.MIN_INTERVAL", 0.1)
    config = WebConfig(tmp_path / "data")
    repo = Repository(config.data_dir)
    runner = Runner(repo, config)
    manager = Sessions(repo, config, runner)
    runner.sessions = manager
    manager.start()
    try:
        yield manager, repo, runner, profile
    finally:
        manager.stop()


def wait_state(manager, states, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = manager.status()
        if result["state"] in states:
            return result
        time.sleep(0.02)
    raise AssertionError(f"Session did not reach {states}: {manager.status()}")


def test_consent_guest_then_automatic_connection_and_private_storage(session_environment):
    manager, repo, _, profile = session_environment
    with pytest.raises(AppError, match="CONSENT_REQUIRED"):
        manager.connect(profile.id, URL, False)
    with pytest.raises(AppError, match="BROWSER_UNAVAILABLE"):
        manager.connect("f" * 24, URL, True)
    manager.connect(profile.id, URL, True)
    deadline = time.monotonic() + 3
    while not (profile.path / "reads").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert manager.status()["state"] == "connecting"
    # Profile data changes after login; no "already logged in" button is needed.
    profile.cookie_file.write_text("member")
    result = wait_state(manager, {"connected"})
    assert result["account_verified"] and result["media_ready"]
    assert COOKIE_SECRET not in json.dumps(result)
    assert manager.credentials(manager.reference)[0]["value"] == COOKIE_SECRET
    frame = sys._current_frames().get(manager.thread.ident)
    while frame and frame.f_code.co_name != "_loop":
        frame = frame.f_back
    assert frame is not None
    assert "cookies" not in frame.f_locals.get("result", {})
    prefs = json.loads(manager.preference.read_text())
    assert prefs == {"profile_id": profile.id}
    assert COOKIE_SECRET.encode() not in repo.path.read_bytes()
    reference = manager.reference
    manager.disconnect()
    assert manager.cookies == [] and manager.status()["state"] == "disconnected"
    with pytest.raises(AppError, match="SESSION_EXPIRED"):
        manager.credentials(reference)
    assert profile.cookie_file.read_text() == "member"


def test_focus_storm_does_not_repeat_cookie_read(session_environment, monkeypatch):
    manager, _, _, profile = session_environment
    monkeypatch.setattr("idlix_downloader.web.sessions.MIN_INTERVAL", 5)
    manager.connect(profile.id, URL, True)
    deadline = time.monotonic() + 3
    while not manager.cookies and time.monotonic() < deadline:
        time.sleep(0.02)
    for _ in range(30):
        manager.focus()
    time.sleep(0.25)
    assert (profile.path / "reads").read_text().count("read") == 1
    assert manager.status()["state"] == "connecting"


def test_permission_retry_without_new_consent_and_verification_is_not_ready(session_environment):
    manager, _, _, profile = session_environment
    profile.cookie_file.write_text("permission")
    manager.connect(profile.id, URL, True)
    assert wait_state(manager, {"error"})["error"]["code"] == "COOKIE_PERMISSION"
    profile.cookie_file.write_text("verification")
    manager.retry()
    result = wait_state(manager, {"error"})
    assert result["account_verified"] and not result["media_ready"]
    assert result["error"]["code"] == "VERIFICATION_REQUIRED"
    profile.cookie_file.write_text("member")
    manager.retry()
    assert wait_state(manager, {"connected"})["media_ready"]
    assert Profile.login.call_count == 1


def test_timeout_stops_probe_and_disconnect_discards_late_result(session_environment, monkeypatch):
    manager, _, _, profile = session_environment
    monkeypatch.setattr("idlix_downloader.web.sessions.CONNECT_TIMEOUT", 0.3)
    profile.cookie_file.write_text("slow")
    manager.connect(profile.id, URL, True)
    assert wait_state(manager, {"error"})["error"]["code"] == "SESSION_TIMEOUT"
    assert manager.tree is None
    manager.disconnect()
    manager.connect(profile.id, URL, True)
    deadline = time.monotonic() + 2
    while manager.tree is None and time.monotonic() < deadline:
        time.sleep(0.01)
    manager.disconnect()
    time.sleep(0.1)
    assert manager.state == "disconnected" and manager.cookies == []


def test_restart_remembers_profile_without_consent_or_connection(session_environment):
    manager, repo, runner, profile = session_environment
    profile.cookie_file.write_text("member")
    manager.connect(profile.id, URL, True)
    wait_state(manager, {"connected"})
    manager.stop()
    reopened = Sessions(repo, manager.config, runner)
    assert reopened.selected == profile.id
    assert reopened.state == "disconnected" and not reopened.cookies and not reopened.reference


def test_context_binding_invalidates_old_inspections_and_revoked_credentials(session_environment):
    manager, repo, _, profile = session_environment
    with manager.binding(URL) as guest:
        assert guest["session_ref"] is None
    profile.cookie_file.write_text("member")
    manager.connect(profile.id, URL, True)
    wait_state(manager, {"connected"})
    with pytest.raises(AppError, match="INSPECTION_EXPIRED"):
        with manager.binding(URL, expected=None, revision=0):
            pass
    reference = manager.reference
    with pytest.raises(AppError, match="SESSION_EXPIRED"):
        with manager.binding("https://another.example/movie/fixture"):
            pass
    manager.cookies[0]["expires"] = 1
    with pytest.raises(AppError, match="SESSION_EXPIRED"):
        manager.credentials(reference)
    manager.expire(reference)
    assert manager.state == "expired" and not manager.cookies
    assert repo.active_ids() == []


def test_disconnect_cancels_queued_jobs_before_forgetting_context(session_environment):
    manager, repo, _, profile = session_environment
    profile.cookie_file.write_text("member")
    manager.connect(profile.id, URL, True)
    wait_state(manager, {"connected"})
    job = repo.enqueue("download", {"url": URL, "session_ref": manager.reference})
    guest = repo.enqueue("download", {"url": URL})
    manager.disconnect()
    assert repo.get(job)["state"] == "cancelled"
    assert repo.get(guest)["state"] == "queued"
    assert not manager.cookies


def test_browser_session_api_rejects_paths_and_requires_csrf(session_environment):
    manager, _, _, profile = session_environment
    config = WebConfig(manager.repository.root.parent / "api-data")
    with TestClient(create_app(config), base_url="http://127.0.0.1:8765") as client:
        body = {"profile_id": profile.id, "url": URL, "consent": True}
        assert client.post("/api/browser-session/connect", json=body).status_code == 403
        client.headers.update(
            {
                "Origin": "http://127.0.0.1:8765",
                "X-CSRF-Token": client.get("/api/session").json()["csrf_token"],
            }
        )
        assert (
            client.post(
                "/api/browser-session/connect", json={**body, "profile": "/tmp"}
            ).status_code
            == 422
        )
        assert (
            client.post("/api/browser-session/connect", json={**body, "consent": False}).status_code
            == 403
        )
        assert client.post("/api/browser-session/connect", json=body).status_code == 200
        assert client.post("/api/browser-session/probe", json={"cookies": []}).status_code == 422
        assert client.delete("/api/browser-session").json()["state"] == "disconnected"
        assert COOKIE_SECRET not in client.get("/api/browser-session").text


def test_rejected_session_stops_without_masking_error_as_timeout(session_environment, monkeypatch):
    manager, _, _, profile = session_environment
    monkeypatch.setattr(
        manager,
        "_probe",
        lambda reference: {
            "code": "SESSION_EXPIRED",
            "account_verified": False,
            "media_ready": False,
        },
    )
    manager.connect(profile.id, URL, True)
    result = wait_state(manager, {"error"})
    assert result["error"]["code"] == "SESSION_EXPIRED"
    assert not result["account_verified"]


def test_second_cli_instance_reports_conflict_without_startup_traceback(tmp_path, capsys):
    from idlix_downloader.cli import main
    from idlix_downloader.web.locking import FileLock

    lock = FileLock(tmp_path / "instance.lock")
    assert lock.acquire()
    try:
        assert main(["web", "--data-dir", str(tmp_path)]) == 1
        captured = capsys.readouterr()
        assert "Instance IdlixPort lain masih aktif" in captured.err
        assert "Traceback" not in captured.err
    finally:
        lock.release()
