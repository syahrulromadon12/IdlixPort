import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from idlix_downloader.downloader import Settings, options
from idlix_downloader.web.errors import AppError, public_error
from idlix_downloader.web.events import Events, SafeLogger
from idlix_downloader.web.locking import FileLock, SlotPool
from idlix_downloader.web.quality import choices, select
from idlix_downloader.web.repository import Repository


def video(height, name="v", **kwargs):
    return dict(
        format_id=name,
        height=height,
        width=height * 16 // 9,
        vcodec="h264",
        acodec="aac",
        protocol="https",
        **kwargs,
    )


def test_quality_survives_format_id_but_not_resolution_change():
    original = {"formats": [video(720, "old"), video(1080, "old-fhd")]}
    expected = next(c for c in choices(original) if c["height"] == 1080)
    selected, _, _ = select({"formats": [video(1080, "new-id")]}, "choice", expected)
    assert selected == expected
    with pytest.raises(AppError, match="QUALITY_CHANGED"):
        select({"formats": [video(720)]}, "choice", expected)


def test_best_maximum_and_ambiguous_choices():
    info = {"formats": [video(720), video(2160, "uhd")]}
    assert select(info, "max_1080")[0]["height"] == 720
    assert select(info, "best")[0]["height"] == 2160
    expected = choices({"formats": [video(720)]})[0]
    with pytest.raises(AppError, match="QUALITY_CHANGED"):
        select({"formats": [video(720, "a"), video(720, "b")]}, "choice", expected)


def test_unknown_drm_and_crop_entitlement():
    crop = dict(format_id="film", width=1920, height=800, vcodec="h264", acodec="aac")
    assert choices({"formats": [crop]})[0]["quality_class"] == 1080
    assert choices({"formats": [crop], "session_max_height": 720}) == []
    assert choices({"formats": [{"height": None}, video(720, has_drm=True)]}) == []
    with pytest.raises(AppError, match="UNKNOWN_RESOLUTION"):
        select({"formats": [{"height": None}]}, "best")


def test_audio_codec_change_invalidates_concrete_choice():
    v = video(1080)
    v["acodec"] = "none"
    audio = dict(format_id="audio", vcodec="none", acodec="aac", abr=128)
    expected = choices({"formats": [v, audio]})[0]
    assert expected["audio_available"] and expected["audio_codec"] == "aac"
    with pytest.raises(AppError, match="QUALITY_CHANGED"):
        select({"formats": [v, {**audio, "acodec": "opus"}]}, "choice", expected)


def test_events_are_bounded_throttled_and_do_not_finish_job():
    output = []
    events = Events(output.append, clock=lambda: 0)
    events.progress({"downloaded_bytes": 12, "url": "https://x/?t=SECRET", "speed": float("nan")})
    events.progress({"downloaded_bytes": 14})
    events.progress({"status": "finished"})
    SafeLogger(events).error("cookie=SECRET")
    assert output == [
        {"type": "progress", "phase": "downloading", "downloaded_bytes": 12},
        {"type": "progress", "phase": "merging"},
    ]
    assert "SECRET" not in json.dumps(output)
    assert "SECRET" not in json.dumps(public_error(RuntimeError("token=SECRET")))

    def hook(event):
        pass

    configured = options(Settings(), progress_hook=hook, postprocessor_hook=hook)
    assert configured["progress_hooks"] == [hook]
    assert "progress_hooks" not in options(Settings())


def test_repository_claims_once_with_independent_slots_and_capacity(tmp_path):
    repo = Repository(tmp_path, limit=3)
    first = repo.enqueue("inspect", {"url": "https://a.example"}, "key")
    assert repo.enqueue("inspect", {"url": "https://a.example"}, "key") == first
    with pytest.raises(AppError, match="CONFLICT"):
        repo.enqueue("inspect", {"url": "https://b.example"}, "key")
    repo.enqueue("inspect", {})
    download = repo.enqueue("download", {})
    with pytest.raises(AppError, match="QUEUE_FULL"):
        repo.enqueue("download", {})
    with ThreadPoolExecutor(4) as threads:
        claimed = list(threads.map(lambda _: repo.claim("inspect"), range(4)))
    assert sum(job is not None for job in claimed) == 1
    assert repo.claim("download")["id"] == download
    repo.cancel(first)
    repo.finish(first, "completed", result={"title": "should not win cancel"})
    assert repo.get(first)["state"] == "cancelled"
    repo.recover()
    assert repo.get(download)["state"] == "interrupted"
    backup = tmp_path / "backup.sqlite3"
    repo.backup(backup)
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 3


def test_schema_and_locks_fail_closed(tmp_path):
    repo = Repository(tmp_path)
    with repo.connection() as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(AppError, match="STORAGE_UNAVAILABLE"):
        Repository(tmp_path)
    first, second = FileLock(tmp_path / "instance"), FileLock(tmp_path / "instance")
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()
    pool = SlotPool(tmp_path / "network", 1)
    lease = pool.acquire()
    with pytest.raises(OSError, match="capacity"):
        pool.acquire(timeout=0)
    lease.release()
    pool.acquire().release()


def test_audio_rendition_with_unknown_codec_remains_available():
    v = video(1080)
    v["acodec"] = "none"
    info = {"formats": [v, {"format_id": "audio", "vcodec": "none", "acodec": None}]}
    choice = choices(info)[0]
    assert choice["audio_available"] and choice["audio_codec"] == "unknown"
    assert len(select(info, "choice", choice)[2]) == 2


def test_network_capacity_is_shared_and_released_after_process_death(tmp_path):
    import subprocess
    import sys

    code = (
        "import sys,time;from idlix_downloader.web.locking import SlotPool;"
        f"lease=SlotPool({str(tmp_path)!r},1).acquire();"
        "print('held',flush=True);time.sleep(60)"
    )
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE)
    try:
        assert child.stdout.readline().strip() == b"held"
        with pytest.raises(OSError, match="capacity"):
            SlotPool(tmp_path, 1).acquire(timeout=0)
    finally:
        child.kill()
        child.wait(timeout=3)
        child.stdout.close()
    SlotPool(tmp_path, 1).acquire(timeout=0).release()


def test_repository_does_not_adopt_unrelated_directory(tmp_path):
    unrelated = tmp_path / "notes.txt"
    unrelated.write_text("keep")
    before = tmp_path.stat().st_mode
    with pytest.raises(AppError, match="STORAGE_UNAVAILABLE"):
        Repository(tmp_path)
    assert unrelated.read_text() == "keep"
    assert tmp_path.stat().st_mode == before
    assert not (tmp_path / "jobs.sqlite3").exists()
