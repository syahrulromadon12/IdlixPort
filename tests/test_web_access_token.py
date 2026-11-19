"""Browser access cookies only authenticate the same source API."""

import urllib.request
from http.cookies import SimpleCookie

import pytest
from yt_dlp import YoutubeDL

from idlix_downloader.web.inspection import WebIdlixIE as IdlixIE
from idlix_downloader.web.network import ScopedRedirectHandler

PAGE = "https://z2.idlixku.com/movie/synthetic-film-2022"


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("https://z2.idlixku.com/api/watch/play-info/movie/fixture", True),
        ("https://provider.example/api/play", False),
        ("https://other.idlixku.com/api/watch/play-info/movie/fixture", False),
        ("http://z2.idlixku.com/api/watch/play-info/movie/fixture", False),
        ("https://z2.idlixku.com/media/video.mp4", False),
    ],
)
def test_access_cookie_bearer_is_scoped_to_same_https_api(monkeypatch, endpoint, expected):

    adapter = IdlixIE(YoutubeDL({"quiet": True}))
    cookies = SimpleCookie("access_token=fixture.access.secret")
    monkeypatch.setattr(adapter, "_get_cookies", lambda url: cookies)
    captured = {}

    def download(*args, **kwargs):
        captured.update(kwargs["headers"])
        return {"ok": True}

    monkeypatch.setattr(adapter, "_download_json", download)
    adapter._api(endpoint, "fixture", PAGE)
    assert (captured.get("Authorization") == "Bearer fixture.access.secret") is expected
    assert "fixture.access.secret" not in captured["Referer"]


def test_guest_api_does_not_send_authorization(monkeypatch):
    adapter = IdlixIE(YoutubeDL({"quiet": True}))
    monkeypatch.setattr(adapter, "_get_cookies", lambda url: {})

    def download(*args, **kwargs):
        assert "Authorization" not in kwargs["headers"]
        return {"ok": True}

    monkeypatch.setattr(adapter, "_download_json", download)
    assert adapter._api("https://z2.idlixku.com/api/watch/play-info/movie/test", "test", PAGE)


@pytest.mark.parametrize(
    "url,kept",
    [
        ("https://z2.idlixku.com/api/watch/next", True),
        ("https://provider.example/api/play", False),
        ("http://z2.idlixku.com/api/watch/next", False),
        ("https://z2.idlixku.com:444/api/watch/next", False),
        ("https://z2.idlixku.com/media/film.mp4", False),
    ],
)
def test_redirect_does_not_leak_authorization(url, kept):
    request = urllib.request.Request(
        "https://z2.idlixku.com/api/watch/play-info/movie/fixture",
        headers={"Authorization": "Bearer synthetic-secret"},
    )
    redirected = ScopedRedirectHandler().redirect_request(request, None, 302, "Found", {}, url)
    assert (redirected.get_header("Authorization") is not None) is kept
