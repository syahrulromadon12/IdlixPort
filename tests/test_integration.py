"""Transfer asli melalui HTTP lokal dengan video sintetis dan FFmpeg."""

import json
import re
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from yt_dlp.utils import download_range_func

from idlix_downloader.downloader import (
    Settings,
    create_downloader,
    download_video,
    download_videos,
    ffmpeg_path,
    inspect_video,
)
from idlix_downloader.extractor import IdlixIE


@pytest.fixture(scope="module")
def media_server(tmp_path_factory):
    binary = ffmpeg_path()
    if not binary:
        pytest.skip("FFmpeg diperlukan untuk tes integrasi media.")
    root = tmp_path_factory.mktemp("media")

    def generate(*arguments):
        subprocess.run(
            [binary, "-hide_banner", "-loglevel", "error", "-y", *arguments],
            check=True,
            capture_output=True,
            timeout=30,
        )

    for height in (720, 1080, 2160):
        generate(
            "-f",
            "lavfi",
            "-i",
            f"color=c=blue:s={height * 16 // 9}x{height}:r=2:d=1",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-preset",
            "ultrafast",
            "-an",
            "-f",
            "hls",
            "-hls_time",
            "1",
            "-hls_list_size",
            "0",
            str(root / f"{height}.m3u8"),
        )
    generate(
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=1",
        "-c:a",
        "aac",
        "-f",
        "hls",
        "-hls_time",
        "1",
        "-hls_list_size",
        "0",
        str(root / "audio.m3u8"),
    )
    master = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="English",DEFAULT=YES,'
        'AUTOSELECT=YES,LANGUAGE="en",URI="audio.m3u8"',
    ]
    for height in (720, 1080, 2160):
        master += [
            f"#EXT-X-STREAM-INF:BANDWIDTH={height * 2000},"
            f'RESOLUTION={height * 16 // 9}x{height},CODECS="avc1.42c033,mp4a.40.2",AUDIO="audio"',
            f"{height}.m3u8",
        ]
    (root / "master.m3u8").write_text("\n".join(master) + "\n")
    generate(
        "-i",
        str(root / "1080.m3u8"),
        "-i",
        str(root / "audio.m3u8"),
        "-c",
        "copy",
        str(root / "direct.mp4"),
    )
    requests = []

    class Handler(SimpleHTTPRequestHandler):
        extensions_map = {
            **SimpleHTTPRequestHandler.extensions_map,
            ".m3u8": "application/vnd.apple.mpegurl",
            ".ts": "video/mp2t",
        }

        def log_message(self, *_args):
            pass

        def do_GET(self):
            requests.append((self.path, self.headers.get("Referer"), self.headers.get("Range")))
            base = f"http://127.0.0.1:{self.server.server_port}"
            if self.path.startswith("/signed/"):
                parts = urlsplit(self.path)
                if parse_qs(parts.query).get("t") != ["fixture-token"]:
                    self.send_error(401)
                    return
                self.path = parts.path.removeprefix("/signed")
            if self.path == "/movie/fixture":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b'<meta property="og:title" content="Synthetic Film">'
                    b'<li data-post="123" data-nume="1" data-type="movie"></li>'
                )
                return
            if self.path == "/protected.m3u8":
                if self.headers.get("Referer") != f"{base}/movie/fixture":
                    self.send_error(403)
                    return
                self.path = "/master.m3u8"
            if self.path == "/direct.mp4" and self.headers.get("Range"):
                data = (root / "direct.mp4").read_bytes()
                start = int(re.fullmatch(r"bytes=(\d+)-", self.headers["Range"]).group(1))
                self.send_response(206)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                self.send_header("Content-Length", str(len(data) - start))
                self.end_headers()
                self.wfile.write(data[start:])
                return
            super().do_GET()

        def do_POST(self):
            if self.path != "/wp-admin/admin-ajax.php":
                self.send_error(404)
                return
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"embed_url": "/protected.m3u8"}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f"http://127.0.0.1:{server.server_port}", root, requests, binary
    server.shutdown()
    server.server_close()
    worker.join(timeout=5)


@pytest.mark.parametrize("quality,height", [("1080", 1080), ("best", 2160)])
def test_hls_download_and_audio_merge(media_server, tmp_path, quality, height):
    base, _, _, binary = media_server
    result = download_video(f"{base}/master.m3u8", Settings(quality=quality, output=tmp_path))
    assert result["height"] == height
    files = list(tmp_path.glob("*.mkv"))
    assert len(files) == 1
    probe = subprocess.run(
        [binary, "-hide_banner", "-i", str(files[0])], capture_output=True, text=True, timeout=10
    )
    assert "Video: h264" in probe.stderr
    assert "Audio: aac" in probe.stderr
    assert f"{height * 16 // 9}x{height}" in probe.stderr


def test_page_adapter_to_referer_protected_hls(media_server):
    base, _, requests, _ = media_server
    info = inspect_video(f"{base}/movie/fixture", Settings(page_adapter=True))
    assert info["title"] == "Synthetic Film"
    assert max(item.get("height") or 0 for item in info["formats"]) == 2160
    assert any(
        path == "/protected.m3u8" and referer == f"{base}/movie/fixture"
        for path, referer, _ in requests
    )


def test_direct_file_resume_uses_range_and_preserves_bytes(media_server, tmp_path):
    base, root, requests, _ = media_server
    data = (root / "direct.mp4").read_bytes()
    filename = "direct [direct] [NAp].mp4"
    (tmp_path / (filename + ".part")).write_bytes(data[:1024])
    download_video(f"{base}/direct.mp4", Settings(quality="best", output=tmp_path))
    files = list(tmp_path.glob("*.mp4"))
    assert len(files) == 1
    assert files[0].read_bytes() == data
    assert any(
        path == "/direct.mp4" and byte_range == "bytes=1024-" for path, _, byte_range in requests
    )


def test_existing_completed_file_is_preserved(media_server, tmp_path):
    base, _, _, _ = media_server
    settings = Settings(quality="best", output=tmp_path)
    download_video(f"{base}/direct.mp4", settings)
    result = next(tmp_path.glob("*.mp4"))
    timestamp = result.stat().st_mtime_ns
    download_video(f"{base}/direct.mp4", settings)
    assert result.stat().st_mtime_ns == timestamp


def test_portable_ffmpeg_supports_external_transfer(media_server, tmp_path):
    base, _, _, binary = media_server
    with create_downloader(
        Settings(quality="best", output=tmp_path, ffmpeg_location=binary)
    ) as downloader:
        downloader.params["download_ranges"] = download_range_func(None, [(0, 0.5)])
        downloader.extract_info(f"{base}/direct.mp4", download=True)
    assert any(file.stat().st_size > 0 for file in tmp_path.glob("*.mp4"))


def test_next_session_propagates_signed_query_to_hls(media_server, tmp_path, monkeypatch):
    base, _, requests, _ = media_server
    with create_downloader(Settings(quality="best", output=tmp_path)) as downloader:
        adapter = IdlixIE(downloader)

        def api(endpoint, *_args, **_kwargs):
            if "/api/movies/" in endpoint:
                return {"id": "signed-fixture", "title": "Signed Fixture", "hasVideo": True}
            if "/play-info/" in endpoint:
                return {
                    "kind": "pentos",
                    "claim": "fixture-claim",
                    "maxHeight": 720,
                    "redeemUrl": "https://player.majorplay.net/api/play",
                }
            return {"code": "ok", "url": f"{base}/signed/master.m3u8?t=fixture-token"}

        monkeypatch.setattr(adapter, "_api", api)
        info = adapter._extract_next_movie(f"{base}/movie/fixture", "fixture")
        result = downloader.process_ie_result(info, download=True)
    assert result["height"] == 720
    assert len(list(tmp_path.glob("*.mkv"))) == 1
    assert any("/signed/7200.ts?t=fixture-token" in path for path, _, _ in requests)


def test_batch_transfers_next_film_after_missing_url(media_server, tmp_path):
    base, _, _, _ = media_server
    results = list(
        download_videos(
            [f"{base}/direct.mp4", f"{base}/missing.mp4", f"{base}/master.m3u8"],
            Settings(quality="best", output=tmp_path),
        )
    )
    assert [result.error is None for result in results] == [True, False, True]
    assert len(list(tmp_path.glob("*.mp4"))) == 1
    assert len(list(tmp_path.glob("*.mkv"))) == 1
