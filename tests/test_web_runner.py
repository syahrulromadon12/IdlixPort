import json
import sys
import time

import pytest

from idlix_downloader.web.config import WebConfig
from idlix_downloader.web.repository import Repository
from idlix_downloader.web.runner import Runner


def wait_for(repo, job_id, states, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = repo.get(job_id)
        if job["state"] in states:
            return job
        time.sleep(0.05)
    raise AssertionError(f"Timed out: {repo.get(job_id)['state']}")


def test_real_worker_blocks_private_network_and_preserves_safe_error(tmp_path):
    repo = Repository(tmp_path)
    job = repo.enqueue("inspect", {"url": "http://127.0.0.1/secret.mp4?t=SECRET"})
    runner = Runner(repo, WebConfig(tmp_path, allowed_hosts=("127.0.0.1",)))
    runner.start()
    try:
        result = wait_for(repo, job, {"failed"})
        assert result["error"]["code"] == "NETWORK_BLOCKED"
        assert "SECRET" not in json.dumps(result["error"])
    finally:
        runner.stop()


def test_inspection_does_not_wait_for_download(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    runner = Runner(repo, WebConfig(tmp_path))
    code = """import sys,json,time
config=json.loads(sys.stdin.readline())
print(json.dumps({'type':'progress','phase':'extracting'}),flush=True)
if config['kind']=='download':
 time.sleep(3)
 print(json.dumps({'type':'error','code':'SOURCE_UNAVAILABLE'}),flush=True)
 sys.exit(1)
print(json.dumps({'type':'result','result':{'title':'Synthetic','choices':[]}}),flush=True)
"""
    monkeypatch.setattr(runner, "command", lambda job: [sys.executable, "-c", code])
    download, inspect = repo.enqueue("download", {}), repo.enqueue("inspect", {})
    runner.start()
    try:
        assert wait_for(repo, inspect, {"completed"})["result"]["title"] == "Synthetic"
        assert repo.get(download)["state"] == "running"
        repo.cancel(download)
        assert wait_for(repo, download, {"cancelled"})["state"] == "cancelled"
    finally:
        runner.stop()
    assert not runner.active


@pytest.mark.parametrize(
    "code",
    [
        "print('not json')",
        "print('a'*70000)",
        "pass",
        "import json,sys;print(json.dumps({'type':'result','result':{'choices':[]}}));sys.exit(1)",
    ],
)
def test_corrupt_or_crashed_worker_cannot_complete(tmp_path, monkeypatch, code):
    repo = Repository(tmp_path)
    runner = Runner(repo, WebConfig(tmp_path))
    monkeypatch.setattr(runner, "command", lambda job: [sys.executable, "-c", code])
    job = repo.enqueue("inspect", {})
    runner.start()
    try:
        assert wait_for(repo, job, {"failed"})["error"]["code"] == "WORKER_FAILED"
    finally:
        runner.stop()


def test_real_worker_rejects_private_source_without_exposing_query(tmp_path):
    repo = Repository(tmp_path)
    runner = Runner(repo, WebConfig(tmp_path, allowed_hosts=("127.0.0.1",)))
    job = repo.enqueue("inspect", {"url": "http://127.0.0.1/private.mp4?token=SECRET"})
    runner.start()
    try:
        end = time.monotonic() + 8
        while repo.get(job)["state"] not in ("completed", "failed") and time.monotonic() < end:
            time.sleep(0.05)
        record = repo.get(job)
        assert record["state"] == "failed"
        assert record["error"]["code"] == "NETWORK_BLOCKED"
        assert "SECRET" not in json.dumps(record["error"])
    finally:
        runner.stop()


def test_shutdown_marks_active_work_interrupted(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    runner = Runner(repo, WebConfig(tmp_path))
    monkeypatch.setattr(
        runner,
        "command",
        lambda job: [sys.executable, "-c", "import sys,time;sys.stdin.readline();time.sleep(60)"],
    )
    job = repo.enqueue("download", {})
    runner.start()
    end = time.monotonic() + 3
    while not runner.active and time.monotonic() < end:
        time.sleep(0.02)
    assert runner.active
    runner.stop()
    assert repo.get(job)["state"] == "interrupted"
    assert not runner.active


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX parent monitor; Windows uses Job Objects"
)
def test_orphan_parent_monitor_stops_descendants(tmp_path):
    import os
    import signal
    import subprocess

    marker = tmp_path / "orphan-continued"
    descendant = (
        "import time;from pathlib import Path;time.sleep(2);"
        f"Path({str(marker)!r}).write_text('bad')"
    )
    child = (
        "import os,subprocess,sys,threading,time;"
        "from idlix_downloader.web.worker import monitor_parent;"
        "threading.Thread(target=monitor_parent,args=(os.getppid(),),daemon=True).start();"
        f"subprocess.Popen([sys.executable,'-c',{descendant!r}]);"
        "print(os.getpid(),flush=True);time.sleep(60)"
    )
    broker_code = (
        "import subprocess,sys,time;"
        f"child=subprocess.Popen([sys.executable,'-c',{child!r}],"
        "stdout=subprocess.PIPE,start_new_session=True);"
        "print(child.stdout.readline().decode().strip(),flush=True);time.sleep(60)"
    )
    broker = subprocess.Popen([sys.executable, "-c", broker_code], stdout=subprocess.PIPE)
    child_pid = None
    try:
        child_pid = int(broker.stdout.readline())
        broker.kill()
        broker.wait(timeout=3)
        time.sleep(2.5)
        assert not marker.exists()
    finally:
        if broker.poll() is None:
            broker.kill()
            broker.wait()
        if child_pid:
            try:
                os.killpg(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        broker.stdout.close()


def test_shutdown_reconciles_a_failed_terminal_commit(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    runner = Runner(repo, WebConfig(tmp_path))
    monkeypatch.setattr(
        runner,
        "command",
        lambda job: [sys.executable, "-c", "import sys,time;sys.stdin.readline();time.sleep(60)"],
    )
    ident = repo.enqueue("download", {})
    original = repo.finish

    def interrupted_commit(*args, **kwargs):
        if runner.stopped.is_set():
            raise RuntimeError("Injected terminal commit failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(repo, "finish", interrupted_commit)
    runner.start()
    deadline = time.monotonic() + 3
    while not runner.active and time.monotonic() < deadline:
        time.sleep(0.02)
    assert runner.active
    runner.stop()
    assert repo.get(ident)["state"] == "interrupted"
    assert runner.failure and not runner.active
