import pytest
from yt_dlp import YoutubeDL
from yt_dlp.utils import ExtractorError

from idlix_downloader.extractor import IdlixIE

PAGE = "https://z2.idlixku.com/movie/synthetic-film-2022"


def fixture_adapter(monkeypatch, *, limit=720, gate=True):
    adapter = IdlixIE(YoutubeDL({"quiet": True}))
    calls = []
    sleeps = []

    def api(endpoint, video_id, page_url, *, body=None):
        calls.append((endpoint, body))
        if "/api/movies/" in endpoint:
            return {"id": "synthetic-id", "title": "Synthetic Film", "hasVideo": True}
        if "/play-info/" in endpoint and gate:
            return {
                "kind": "gate",
                "serverNow": 1000,
                "unlockAt": 16000,
                "gateToken": "fixture-gate",
            }
        if endpoint.endswith("/api/play"):
            return {
                "code": "ok",
                "url": "https://cdn.example/master.m3u8?t=fixture-token&pm=b",
                "subtitles": [
                    {"lang": "id", "label": "Indonesian", "path": "https://cdn.example/sub.vtt"},
                ],
            }
        return {
            "kind": "pentos",
            "claim": "fixture-claim",
            "viewerTier": "guest",
            "maxHeight": limit,
            "redeemUrl": "https://player.majorplay.net/api/play",
        }

    monkeypatch.setattr(adapter, "_api", api)
    monkeypatch.setattr(adapter, "_sleep", lambda delay, *args, **kwargs: sleeps.append(delay))
    monkeypatch.setattr(
        adapter,
        "_extract_m3u8_formats_and_subtitles",
        lambda *args, **kwargs: (
            [
                {"format_id": "360p", "url": "https://cdn.example/360.m3u8"},
                {"format_id": "720p", "url": "https://cdn.example/720.m3u8"},
                {"format_id": "1080p", "url": "https://cdn.example/1080.m3u8"},
            ],
            {},
        ),
    )
    return adapter, calls, sleeps


def test_next_session_waits_normally_and_respects_entitlement(monkeypatch):
    adapter, calls, sleeps = fixture_adapter(monkeypatch)
    result = adapter._extract_next_movie(PAGE, "synthetic-film-2022")
    assert sleeps == [15.5]
    assert [item["height"] for item in result["formats"]] == [360, 720]
    assert result["session_max_height"] == 720
    assert result["session_viewer_tier"] == "guest"
    assert result["formats"][0]["url"].endswith("?t=fixture-token&pm=b")
    assert result["formats"][0]["extra_param_to_segment_url"] == "t=fixture-token&pm=b"
    assert result["subtitles"]["id"][0]["url"] == "https://cdn.example/sub.vtt"
    assert calls[2][1] == {"gateToken": "fixture-gate"}
    assert calls[3][1] == {"claim": "fixture-claim", "mode": "browser"}


def test_entitled_session_allows_1080(monkeypatch):
    adapter, _, sleeps = fixture_adapter(monkeypatch, limit=1080, gate=False)
    result = adapter._extract_next_movie(PAGE, "synthetic-film-2022")
    assert max(item["height"] for item in result["formats"]) == 1080
    assert sleeps == []


def test_native_adapter_recognizes_next_movie_page(monkeypatch):
    adapter, _, _ = fixture_adapter(monkeypatch)
    monkeypatch.setattr(
        adapter, "_download_webpage", lambda *args: '<script src="/_next/static/x.js">'
    )
    result = adapter._real_extract(PAGE)
    assert result["title"] == "Synthetic Film"


def test_rejects_unknown_entitlement(monkeypatch):
    adapter, _, _ = fixture_adapter(monkeypatch, limit=None)
    with pytest.raises(ExtractorError, match="Batas resolusi"):
        adapter._extract_next_movie(PAGE, "synthetic-film-2022")


def test_api_rejection_does_not_expose_tokens(monkeypatch):
    adapter = IdlixIE(YoutubeDL({"quiet": True}))
    monkeypatch.setattr(
        adapter,
        "_download_json",
        lambda *args, **kwargs: {
            "error": "rejected: secret-token-value",
        },
    )
    with pytest.raises(ExtractorError, match="API IDLIX menolak sesi") as error:
        adapter._api("https://idlix.example/api", "fixture", PAGE)
    assert "secret-token-value" not in str(error.value)


def test_bitrate_only_variants_are_probed_before_filtering(monkeypatch):
    adapter, _, _ = fixture_adapter(monkeypatch)
    monkeypatch.setattr(
        adapter,
        "_extract_m3u8_formats_and_subtitles",
        lambda *a, **k: (
            [
                {"format_id": "204", "url": "https://cdn.example/low.m3u8"},
                {"format_id": "943", "url": "https://cdn.example/high.m3u8"},
            ],
            {},
        ),
    )
    probed = []

    def probe(item, *_args):
        probed.append(item["format_id"])
        item["height"] = 360 if item["format_id"] == "204" else 720

    monkeypatch.setattr(adapter, "_probe_dimensions", probe)
    result = adapter._extract_next_movie(PAGE, "fixture")
    assert probed == ["204", "943"]
    assert [item["height"] for item in result["formats"]] == [360, 720]


def test_probe_is_bounded_and_preserves_authentication(monkeypatch):
    from types import SimpleNamespace

    adapter, _, _ = fixture_adapter(monkeypatch)
    reads = []
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self, amount):
            reads.append(amount)
            return b"x" * amount

    def open_request(request):
        requests.append(request)
        return Response()

    def run(command, **kwargs):
        assert len(kwargs["input"]) == 262144
        assert kwargs["timeout"] == 15
        return SimpleNamespace(stderr=b"Stream #0:0: Video: h264, yuv420p, 1920x800 [SAR 1:1]")

    monkeypatch.setattr(
        "idlix_downloader.extractor.FFmpegPostProcessor",
        lambda *_args: SimpleNamespace(available=True, executable="ffmpeg"),
    )
    monkeypatch.setattr(adapter, "_download_webpage", lambda *a, **k: "#EXTM3U\npart.ts\n")
    monkeypatch.setattr(adapter._downloader, "urlopen", open_request)
    monkeypatch.setattr("idlix_downloader.extractor.subprocess.run", run)
    item = {
        "url": "https://cdn.example/list.m3u8?t=fixture",
        "extra_param_to_segment_url": "t=fixture",
    }
    adapter._probe_dimensions(item, "fixture", PAGE)
    assert reads == [262144]
    assert requests[0].url == "https://cdn.example/part.ts?t=fixture"
    assert requests[0].headers["Range"] == "bytes=0-262143"
    assert (item["width"], item["height"]) == (1920, 800)
    assert item["session_required_height"] == 1080
