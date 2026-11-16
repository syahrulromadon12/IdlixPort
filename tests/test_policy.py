from pathlib import Path

import pytest
from yt_dlp import YoutubeDL

from idlix_downloader.cli import main
from idlix_downloader.downloader import (
    Settings,
    format_selector,
    options,
    single_video,
    validate_url,
)


def select(quality, formats):
    with YoutubeDL(
        {"quiet": True, "format": format_selector(quality), "format_sort": ["res", "fps", "br"]}
    ) as downloader:
        return downloader.process_ie_result(
            {
                "id": "fixture",
                "title": "Fixture",
                "formats": formats,
                "extractor": "fixture",
                "extractor_key": "Fixture",
            },
            download=False,
        )


def media(format_id, height, *, audio=False, tbr=1000):
    return {
        "format_id": format_id,
        "url": f"https://media.example/{format_id}.mp4",
        "ext": "mp4",
        "height": height,
        "width": height * 16 // 9 if height else None,
        "vcodec": "h264",
        "acodec": "aac" if audio else "none",
        "tbr": tbr,
    }


@pytest.mark.parametrize("quality,expected", [("1080", "fhd"), ("best", "uhd"), ("720", "hd")])
def test_quality_selects_highest_within_limit(quality, expected):
    result = select(
        quality,
        [
            media("hd", 720, audio=True),
            media("fhd", 1080, audio=True),
            media("uhd", 2160, audio=True),
        ],
    )
    assert result["format_id"] == expected


def test_prefers_resolution_before_bitrate_and_merges_audio():
    formats = [
        media("hd", 720, tbr=9000),
        media("fhd", 1080, tbr=2000),
        {
            "format_id": "audio",
            "url": "https://media.example/audio.m4a",
            "ext": "m4a",
            "vcodec": "none",
            "acodec": "aac",
            "abr": 128,
        },
    ]
    result = select("1080", formats)
    assert result["format_id"] == "fhd+audio"
    assert [item["format_id"] for item in result["requested_formats"]] == ["fhd", "audio"]


def test_unknown_height_cannot_silently_exceed_cap():
    from yt_dlp.utils import ExtractorError

    with pytest.raises(ExtractorError, match="Requested format is not available"):
        select("1080", [media("unknown", None, audio=True)])
    assert select("best", [media("unknown", None, audio=True)])["format_id"] == "unknown"


def test_lower_resolution_fallback():
    assert select("1080", [media("hd", 720, audio=True)])["height"] == 720


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://host/movie",
        "http://",
        "https://u:p@example.com/",
        "https://example.com:bad/",
        "https://example.com/hello world",
        "https://example.com/\n",
    ],
)
def test_rejects_invalid_urls(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_accepts_signed_urls_without_altering_token():
    url = "https://cdn.example/master.m3u8?token=a%2Fb&expires=123"
    assert validate_url(url) == url


@pytest.mark.parametrize(
    "values",
    [
        {"connections": 0},
        {"connections": 17},
        {"server": 0},
        {"quality": "999"},
        {"cookies": Path("/nonexistent/cookies.txt")},
    ],
)
def test_rejects_invalid_settings(values):
    with pytest.raises(ValueError):
        Settings(**values)


@pytest.mark.parametrize(
    "info", [None, {"_type": "playlist"}, {"_type": "multi_video"}, {"is_live": True}]
)
def test_single_film_guard(info):
    with pytest.raises(ValueError):
        single_video(info)


def test_no_silent_partial_success():
    configured = options(Settings())
    assert configured["skip_unavailable_fragments"] is False
    assert configured["continuedl"] is True
    assert configured["overwrites"] is False


def test_cli_invalid_settings_is_actionable(capsys):
    assert main(["download", "https://example.com/movie", "-N", "0"]) == 1
    assert "antara 1 dan 16" in capsys.readouterr().err


def test_browser_cookie_failure_returns_error_without_traceback(monkeypatch, capsys):
    from yt_dlp.cookies import CookieLoadError

    def fail(*_args):
        raise CookieLoadError("Browser cookies tidak dapat dibaca.")

    monkeypatch.setattr("idlix_downloader.cli.inspect_video", fail)
    assert main(["info", "https://example.com/movie", "--cookies-from-browser", "safari"]) == 1
    assert "Gagal: Browser cookies tidak dapat dibaca." in capsys.readouterr().err
