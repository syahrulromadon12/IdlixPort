"""Use real HTTP Range transfers while preserving the production network guard."""

import hashlib
import json
from unittest.mock import Mock

import pytest
from test_integration import media_server as media_server
from test_web_network import config
from test_web_network import public_route as public_route
from yt_dlp.utils import DownloadError

from idlix_downloader.web.engine import run_job
from idlix_downloader.web.errors import AppError
from idlix_downloader.web.events import Events
from idlix_downloader.web.resume import prepare_resume


@pytest.mark.parametrize("changed", [False, True])
def test_http_range_resume_requires_matching_strong_identity(public_route, tmp_path, changed):
    root, requests, _ = public_route
    cfg = config(tmp_path / "old", "http://source.example/direct.mp4")
    run_job(cfg, Events(lambda event: None))
    old = tmp_path / "old/output"
    data = (old / "media.mp4").read_bytes()
    assert (old / ".resume.json").is_file()
    (old / "media.mp4").unlink()
    (old / "media.mp4.part").write_bytes(data[:1024])
    if changed:
        sidecar = json.loads((old / ".resume.json").read_text())
        sidecar["etag"] = '"old-representation"'
        (old / ".resume.json").write_text(json.dumps(sidecar))
    requests.clear()
    cfg = config(tmp_path / "new", "http://source.example/direct.mp4")
    cfg["resume_from"] = str(old)
    result = run_job(cfg, Events(lambda event: None))
    assert result["resumed"] is (not changed)
    assert (
        hashlib.sha256((tmp_path / "new/output/media.mp4").read_bytes()).digest()
        == hashlib.sha256(data).digest()
    )
    assert ("bytes=1024-" in [row[2] for row in requests]) is (not changed)
    assert (old / "media.mp4.part").read_bytes() == data[:1024]
    assert "http://" not in (tmp_path / "new/output/.resume.json").read_text()


def test_hls_new_token_starts_fresh_and_missing_source_fails(public_route, tmp_path):
    _, requests, _ = public_route
    old = tmp_path / "old"
    old.mkdir()
    (old / "media.mp4.part").write_bytes(b"incompatible fragments")
    cfg = config(tmp_path / "new", "http://source.example/master.m3u8?token=fresh")
    cfg["resume_from"] = str(old)
    result = run_job(cfg, Events(lambda event: None))
    assert result["effective_quality"]["quality_class"] == 1080 and not result["resumed"]
    assert any("token=fresh" in row[0] for row in requests)
    assert (old / "media.mp4.part").read_bytes() == b"incompatible fragments"
    with pytest.raises(DownloadError):
        run_job(
            config(tmp_path / "gone", "http://source.example/missing.m3u8"),
            Events(lambda event: None),
        )


def test_changed_explicit_quality_is_not_resumed_or_downgraded(public_route, tmp_path):
    inspection = run_job(
        config(tmp_path / "inspect", "http://source.example/master.m3u8", "inspect"),
        Events(lambda event: None),
    )
    choice = next(item for item in inspection["choices"] if item["quality_class"] == 1080)
    choice = {**choice, "height": 1000}
    cfg = config(tmp_path / "new", "http://source.example/master.m3u8")
    cfg["payload"].update(policy="choice", choice=choice)
    with pytest.raises(AppError, match="QUALITY_CHANGED"):
        run_job(cfg, Events(lambda event: None))
    assert not list((tmp_path / "new/output").glob("*.mp4"))


def test_segmented_or_weakly_identified_media_never_reuses_partials(tmp_path):
    downloader = Mock()
    assert not prepare_resume(downloader, [{"protocol": "m3u8_native"}], {}, tmp_path)
    downloader.urlopen.assert_not_called()
