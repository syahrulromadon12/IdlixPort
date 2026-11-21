"""Bounded subtitle data, language policy and safe partial mux failures."""

import shutil
from pathlib import Path

import pytest
from test_integration import media_server as media_server
from test_web_network import config
from test_web_network import public_route as public_route

from idlix_downloader.web import subtitles
from idlix_downloader.web.engine import finalize
from idlix_downloader.web.errors import AppError
from idlix_downloader.web.events import Events


@pytest.mark.parametrize(
    "languages,expected",
    [
        ({"en"}, ("off", None)),
        ({"id", "en"}, ("embed", "id")),
        ({"id-id", "en"}, ("embed", "id-id")),
    ],
)
def test_auto_only_selects_indonesian(languages, expected):
    assert subtitles.selection("auto", None, languages) == expected
    assert subtitles.language_code("ind_ID") == "id-id"


@pytest.mark.parametrize("failure", ["mux", "oversize", "private_url"])
def test_subtitle_failure_keeps_valid_video(public_route, tmp_path, monkeypatch, failure):
    root, _, _ = public_route
    output = tmp_path / "output"
    output.mkdir()
    video = output / "media.mp4"
    shutil.copyfile(root / "direct.mp4", video)
    before = video.read_bytes()
    caption = root / "caption.srt"
    caption.write_text("1\n00:00:00,000 --> 00:00:00,800\nSynthetic subtitle\n\n")
    if failure == "oversize":
        caption.write_bytes(b"x" * (subtitles.MAX_SUBTITLE_BYTES + 1))
    url = (
        "http://127.0.0.1/private.srt"
        if failure == "private_url"
        else "http://source.example/caption.srt"
    )
    if failure == "mux":
        original = subtitles.local_ffmpeg

        def fail_merge(args, **kwargs):
            if Path(args[-1]).name == "film.mkv":
                raise AppError("SUBTITLE_FAILED")
            return original(args, **kwargs)

        monkeypatch.setattr(subtitles, "local_ffmpeg", fail_merge)
    cfg = config(tmp_path, "http://source.example/master.m3u8")
    cfg["payload"].update(subtitle_mode="embed", subtitle_language="id")
    result = finalize(
        cfg,
        Events(lambda event: None),
        video,
        {"evidence": "dimensions", "width": 1920, "height": 1080, "quality_class": 1080},
        {"id": "fixture", "subtitles": {"id": [{"url": url, "ext": "srt"}]}},
    )
    assert result["partial"]
    assert result["subtitle_error"] == (
        "NETWORK_BLOCKED" if failure == "private_url" else "SUBTITLE_FAILED"
    )
    assert video.read_bytes() == before and not (output / "film.mkv").exists()
    assert result["artifacts"] == [{"name": "media.mp4", "size_bytes": len(before)}]
