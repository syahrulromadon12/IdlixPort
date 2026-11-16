from urllib.parse import parse_qs

import pytest
from yt_dlp import YoutubeDL
from yt_dlp.utils import ExtractorError, unsmuggle_url

from idlix_downloader.extractor import IdlixIE, PlayerParser, player_url

PAGE_URL = "https://z2.idlixku.com/movie/the-last-10-years-2022"
PAGE = """
<meta property="og:title" content="The Last 10 Years (2022)">
<li data-type="movie" data-nume="trailer" data-post="123"></li>
<li data-type="movie" data-nume="1" data-post="123"></li>
<li data-nume="2" data-post="123" data-type="movie"></li>
"""


def extractor(server=1):
    downloader = YoutubeDL({"quiet": True, "extractor_args": {"idlix": {"server": [str(server)]}}})
    return IdlixIE(downloader)


def test_collects_movie_servers_skipping_trailer():
    parser = PlayerParser()
    parser.feed(PAGE)
    assert [server["nume"] for server in parser.servers] == ["1", "2"]


def test_selects_requested_server_and_preserves_referer(monkeypatch):
    adapter = extractor(2)
    monkeypatch.setattr(adapter, "_download_webpage", lambda *args: PAGE)
    calls = []

    def response(url, video_id, **kwargs):
        calls.append((url, kwargs))
        return {"embed_url": '<iframe src="https://player.example/embed/123?a=1&amp;b=2"></iframe>'}

    monkeypatch.setattr(adapter, "_download_json", response)
    result = adapter._real_extract(PAGE_URL)
    target, data = unsmuggle_url(result["url"])
    assert target == "https://player.example/embed/123?a=1&b=2"
    assert data["referer"] == PAGE_URL
    assert result["title"] == "The Last 10 Years (2022)"
    assert calls[0][0] == "https://z2.idlixku.com/wp-admin/admin-ajax.php"
    assert parse_qs(calls[0][1]["data"].decode())["nume"] == ["2"]


def test_static_pages_use_generic_extractor(monkeypatch):
    adapter = extractor()
    monkeypatch.setattr(adapter, "_download_webpage", lambda *args: "<video src='/film.mp4'>")
    result = adapter._real_extract(PAGE_URL)
    assert result["ie_key"] == "Generic"
    assert unsmuggle_url(result["url"])[0] == PAGE_URL


def test_server_out_of_range(monkeypatch):
    adapter = extractor(3)
    monkeypatch.setattr(adapter, "_download_webpage", lambda *args: PAGE)
    with pytest.raises(ExtractorError, match="1 sampai 2"):
        adapter._real_extract(PAGE_URL)


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "data:text/html,test",
        "",
        None,
        '<iframe src="javascript:alert(1)"></iframe>',
        '<iframe src="/a"></iframe><iframe src="/b"></iframe>',
    ],
)
def test_rejects_malformed_player_responses(value):
    with pytest.raises(ExtractorError):
        player_url(value, PAGE_URL)


def test_relative_player_url():
    assert player_url("//player.example/watch/1", PAGE_URL) == "https://player.example/watch/1"


def test_target_domain_selected_but_direct_media_uses_generic():
    assert IdlixIE.suitable(PAGE_URL)
    assert not IdlixIE.suitable("https://cdn.idlixku.com/master.m3u8?token=123")
