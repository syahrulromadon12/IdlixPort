"""P3 cancellation, bounded attempts, restart and result lifecycle proofs."""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from test_integration import media_server as media_server
from test_web_flow import flow_app as flow_app
from test_web_flow import flow_client as flow_client
from test_web_flow import inspect, wait_job
from test_web_lifecycle import artifact
from test_web_lifecycle import service as service
from test_web_runner import wait_for

from idlix_downloader.web.config import WebConfig
from idlix_downloader.web.errors import AppError, public_error
from idlix_downloader.web.repository import MAX_ATTEMPTS, Repository
from idlix_downloader.web.runner import Runner


def failed(repo):
    ident = repo.enqueue("download", {"url": "https://source.example/media.mp4", "policy": "best"})
    assert repo.claim("download")["id"] == ident
    repo.finish(ident, "failed", error=AppError("SOURCE_UNAVAILABLE").public())
    return ident


def test_bounded_retry_idempotency_and_concurrent_clicks(tmp_path):
    repo = Repository(tmp_path)
    parent = failed(repo)
    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(lambda _: repo.retry(parent, "same-key"), range(6)))
    assert len(set(ids)) == 1
    child = ids[0]
    assert repo.get(child)["payload"]["retry_of"] == parent
    assert repo.get(child)["payload"]["attempt"] == 2
    assert repo.retry(parent, "same-key") == child
    with pytest.raises(AppError, match="CONFLICT"):
        repo.retry(parent, "different-key")
    assert repo.claim("download")["id"] == child
    repo.finish(child, "interrupted")
    last = repo.retry(child)
    assert repo.claim("download")["id"] == last
    repo.finish(last, "failed")
    assert repo.get(last)["payload"]["attempt"] == MAX_ATTEMPTS
    assert not repo.retry_available(last)
    with pytest.raises(AppError, match="RETRY_LIMIT"):
        repo.retry(last)


def test_cancel_complete_race_is_transactional(tmp_path):
    repo = Repository(tmp_path)
    for _ in range(12):
        ident = repo.enqueue("download", {})
        repo.claim("download")
        barrier = Barrier(2)

        def finish(barrier=barrier, ident=ident):
            barrier.wait()
            repo.finish(
                ident, "completed", result={"artifacts": [{"name": "media.mp4", "size_bytes": 7}]}
            )

        def cancel(barrier=barrier, ident=ident):
            barrier.wait()
            repo.cancel(ident)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(finish), pool.submit(cancel)]
            for future in futures:
                future.result()
        job = repo.get(ident)
        assert job["state"] in ("completed", "cancelled")
        assert bool(repo.artifacts_for(ident)) == (job["state"] == "completed")
        repo.cancel(ident)
        assert repo.get(ident)["state"] == job["state"]


@pytest.mark.parametrize("phase", ["downloading", "merging"])
def test_cancel_stops_worker_tree_before_terminal(tmp_path, monkeypatch, phase):
    repo = Repository(tmp_path / "data")
    runner = Runner(repo, WebConfig(repo.root))
    marker = tmp_path / "orphan"
    child = (
        "import time;from pathlib import Path;time.sleep(2);"
        f"Path({str(marker)!r}).write_text('bad')"
    )
    code = f"""import sys,json,subprocess,time
config=json.loads(sys.stdin.readline())
subprocess.Popen([sys.executable,'-c',{child!r}])
print(json.dumps({{'type':'progress','phase':{phase!r}}}),flush=True)
time.sleep(60)
"""
    monkeypatch.setattr(runner, "command", lambda job: [sys.executable, "-c", code])
    ident = repo.enqueue("download", {})
    runner.start()
    try:
        deadline = time.monotonic() + 5
        while repo.get(ident)["phase"] != phase and time.monotonic() < deadline:
            time.sleep(0.02)
        assert repo.get(ident)["phase"] == phase
        process = runner.active[ident].process
        repo.cancel(ident)
        assert wait_for(repo, ident, {"cancelled"})["result"] is None
        assert process.poll() is not None and not repo.artifacts_for(ident)
        time.sleep(2.1)
        assert not marker.exists()
    finally:
        runner.stop()


def test_restart_preserves_queued_and_interrupts_active(tmp_path):
    repo = Repository(tmp_path)
    running = repo.enqueue("download", {})
    repo.claim("download")
    cancelling = repo.enqueue("inspect", {})
    repo.claim("inspect")
    repo.cancel(cancelling)
    queued = repo.enqueue("download", {})
    repo.recover()
    reopened = Repository(tmp_path)
    assert reopened.get(running)["state"] == "interrupted"
    assert reopened.get(cancelling)["state"] == "interrupted"
    assert reopened.get(queued)["state"] == "queued"


def test_delete_results_respects_stream_lease_retry_and_isolation(service):
    ident, path, job = artifact(service)
    other_ident, other_path, other_job = artifact(service)
    lease, _ = service.artifacts.acquire(ident)
    try:
        with pytest.raises(AppError, match="CONFLICT"):
            service.artifacts.delete_results(job)
    finally:
        lease.close()
    service.artifacts.delete_results(job)
    assert not path.exists() and other_path.exists()
    assert service.repository.get(job)["state"] == "completed"
    assert service.snapshot(job)["storage"]["files_deleted"]
    service.artifacts.delete_results(job)
    parent = failed(service.repository)
    partial = service.destinations.prepare("default", parent) / "media.mp4.part"
    partial.write_bytes(b"partial")
    child = service.retry(parent)
    with pytest.raises(AppError, match="CONFLICT"):
        service.artifacts.delete_results(parent)
    service.repository.cancel(child)
    service.artifacts.delete_results(parent)
    assert not partial.exists() and other_path.exists()
    assert service.repository.get(parent)["state"] == "failed"


def test_history_keeps_active_jobs_beyond_recent_window(flow_client):
    client, app, *_ = flow_client
    # Stop consumption only: API remains live with its repository during this test.
    app.state.runner.stop()
    repo = app.state.repository
    oldest = repo.enqueue("download", {"title": "old active"})
    for _ in range(24):
        failed_inspect = repo.enqueue("inspect", {"url": "http://source.example/master.m3u8"})
        repo.claim("inspect")
        repo.finish(failed_inspect, "failed")
    assert oldest in [job["id"] for job in client.get("/api/jobs").json()["jobs"]]
    for _ in range(12):
        repo.cancel(repo.enqueue("download", {"title": "history"}))
    first = client.get("/api/history?limit=10").json()
    second = client.get("/api/history?limit=10&offset=10").json()
    assert first["total"] == 13 and first["next_offset"] == 10
    assert len(first["jobs"]) == 10 and len(second["jobs"]) == 3
    assert not set(job["id"] for job in first["jobs"]) & set(job["id"] for job in second["jobs"])
    assert client.get("/api/history?limit=1000").status_code == 422
    assert client.post(f"/api/jobs/{oldest}/cancel", json={"path": "/tmp"}).status_code == 422
    assert client.post(f"/api/jobs/{oldest}/cancel").json()["state"] == "cancelled"
    assert client.post("/api/jobs/" + "f" * 32 + "/cancel").status_code == 404


def test_api_retry_reextracts_after_inspection_expiry(flow_client):
    client, app, *_ = flow_client
    job = inspect(client)
    response = client.post("/api/jobs", json={"inspection_id": job["id"], "policy": "max_1080"})
    ident = response.json()["id"]
    client.post(f"/api/jobs/{ident}/cancel")
    assert wait_job(client, ident)["state"] == "cancelled"
    with app.state.repository.connection() as db:
        db.execute("UPDATE inspections SET expires=1 WHERE job_id=?", (job["id"],))
    retried = client.post(f"/api/jobs/{ident}/retry", headers={"Idempotency-Key": "retry-expired"})
    assert retried.status_code == 202
    new = retried.json()["id"]
    assert (
        client.post(
            f"/api/jobs/{ident}/retry", headers={"Idempotency-Key": "retry-expired"}
        ).json()["id"]
        == new
    )
    completed = wait_job(client, new)
    assert completed["state"] == "completed" and completed["attempt"] == 2
    assert completed["effective_quality"]["quality_class"] == 1080
    assert not completed["resumed"]  # HLS never reuses old fragments.
    assert client.delete(f"/api/jobs/{new}/files").status_code == 204
    assert client.get(f"/api/jobs/{new}").json()["storage"]["files_deleted"]


def test_specific_safe_source_errors():
    assert public_error(Exception("HTTP Error 403: token=SECRET"))["code"] == "SESSION_EXPIRED"
    assert public_error(OSError(28, "No space left"))["code"] == "DISK_FULL"
    assert "SECRET" not in json.dumps(public_error(Exception("HTTP Error 401: SECRET")))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal escalation; Windows unverified")
def test_stubborn_worker_is_killed_after_five_second_grace():
    from idlix_downloader.web.processes import ProcessTree

    tree = ProcessTree(
        [
            sys.executable,
            "-c",
            "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            "print('ready',flush=True);time.sleep(60)",
        ]
    )
    try:
        assert tree.process.stdout.readline().strip() == b"ready"
        started = time.monotonic()
        tree.stop()
        elapsed = time.monotonic() - started
        assert 4.8 <= elapsed < 8
        assert tree.process.poll() is not None
    finally:
        tree.stop()
        tree.close()


def test_runner_restart_retains_partials_and_only_restarts_queued(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    code = """import json,sys,time
from pathlib import Path
config=json.loads(sys.stdin.readline())
Path(config['output'],'media.mp4.part').write_bytes(b'partial')
print(json.dumps({'type':'progress','phase':'downloading'}),flush=True)
time.sleep(60)
"""
    runner = Runner(repo, WebConfig(tmp_path))
    monkeypatch.setattr(runner, "command", lambda job: [sys.executable, "-c", code])
    active = repo.enqueue("download", {})
    queued = repo.enqueue("download", {})
    runner.start()
    deadline = time.monotonic() + 5
    while repo.get(active)["phase"] != "downloading" and time.monotonic() < deadline:
        time.sleep(0.02)
    runner.stop()
    assert repo.get(active)["state"] == "interrupted"
    assert repo.get(queued)["state"] == "queued"
    partial = tmp_path / "downloads" / active / "media.mp4.part"
    assert partial.read_bytes() == b"partial"
    reopened = Repository(tmp_path)
    next_runner = Runner(reopened, WebConfig(tmp_path))
    monkeypatch.setattr(
        next_runner,
        "command",
        lambda job: [
            sys.executable,
            "-c",
            "import sys,json;sys.stdin.readline();"
            "print(json.dumps({'type':'error','code':'SOURCE_UNAVAILABLE'}));sys.exit(1)",
        ],
    )
    next_runner.start()
    try:
        wait_for(reopened, queued, {"failed"})
        assert reopened.get(active)["state"] == "interrupted"
        assert partial.read_bytes() == b"partial"
    finally:
        next_runner.stop()
