import http.server
import ipaddress
import shutil
import socket
import ssl
import subprocess
import threading
from unittest.mock import Mock

import pytest
from test_integration import media_server as media_server
from yt_dlp import YoutubeDL
from yt_dlp.downloader.external import FFmpegFD
from yt_dlp.networking import Request
from yt_dlp.networking.exceptions import RequestError

from idlix_downloader.web.engine import media_downloader, run_job
from idlix_downloader.web.errors import AppError, public_error
from idlix_downloader.web.events import Events
from idlix_downloader.web.locking import SlotPool
from idlix_downloader.web.network import NetworkPolicy, install_transport, restricted_handler


@pytest.fixture
def public_route(media_server, monkeypatch):
    base, root, requests, binary = media_server
    port = int(base.rsplit(":", 1)[1])
    original_lookup, original_socket = socket.getaddrinfo, socket.socket

    def lookup(host, target_port, *args, **kwargs):
        if host == "source.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", target_port))]
        return original_lookup(host, target_port, *args, **kwargs)

    class TestSocket(original_socket):
        def connect(self, target):
            if target[0] == "1.1.1.1":
                target = ("127.0.0.1", port)
            return super().connect(target)

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(socket, "socket", TestSocket)
    return root, requests, binary


def config(tmp_path, url, kind="download"):
    return {
        "kind": kind,
        "payload": {"url": url, "policy": "max_1080"},
        "output": str(tmp_path / "output"),
        "allowed_hosts": ["source.example"],
        "lock_dir": str(tmp_path / "locks"),
        "network_slots": 4,
    }


def test_guarded_native_hls_download_and_merge(public_route, tmp_path):
    _, requests, binary = public_route
    result = run_job(
        config(tmp_path, "http://source.example/master.m3u8"), Events(lambda event: None)
    )
    assert result["effective_quality"]["quality_class"] == 1080
    assert result["artifacts"]
    probe = subprocess.run(
        [binary, "-hide_banner", "-i", str(tmp_path / "output" / result["artifacts"][0]["name"])],
        capture_output=True,
        text=True,
    )
    assert "1920x1080" in probe.stderr and "Audio: aac" in probe.stderr
    assert any(path.endswith(".ts") for path, _, _ in requests)


def test_segment_recovers_after_three_transient_failures(public_route, tmp_path):
    root, requests, _ = public_route
    playlist = root / "1080.m3u8"
    playlist.write_text(playlist.read_text().replace("10800.ts", "transient.ts"))

    result = run_job(
        config(tmp_path, "http://source.example/master.m3u8"), Events(lambda event: None)
    )

    assert result["effective_quality"]["quality_class"] == 1080
    assert result["artifacts"]
    assert sum(path == "/transient.ts" for path, _, _ in requests) == 4


def test_nested_manifest_cannot_escape_guard(public_route, tmp_path):
    root, _, _ = public_route
    (root / "evil.m3u8").write_text(
        "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100000,RESOLUTION=640x360\n"
        "http://127.0.0.1/private.m3u8\n"
    )
    with pytest.raises(Exception) as failure:
        run_job(config(tmp_path, "http://source.example/evil.m3u8"), Events(lambda event: None))
    assert public_error(failure.value)["code"] == "NETWORK_BLOCKED_DESTINATION"


def test_error_responses_release_capacity_and_external_downloader_blocked(public_route, tmp_path):
    cfg = config(tmp_path, "http://source.example/master.m3u8")
    with media_downloader(cfg, tmp_path / "output", Events(lambda event: None)) as d:
        with pytest.raises(Exception) as failure:
            d.urlopen(Request("http://source.example/not-found"))
        failure.value.response.close()
        pool = SlotPool(tmp_path / "locks", 4)
        leases = [pool.acquire(timeout=0) for _ in range(4)]
        for lease in leases:
            lease.release()
        with pytest.raises(AppError, match="NETWORK_BLOCKED"):
            FFmpegFD(d, d.params).real_download("unused", {"url": "http://source.example/x"})
        with pytest.raises(AppError, match="NETWORK_BLOCKED"):
            d.dl("unused", {"url": "http://source.example/x", "protocol": "m3u8"})
        for scheme in ("file:///etc/passwd", "ftp://source.example/file", "data:text/plain,secret"):
            with pytest.raises(RequestError):
                d.urlopen(Request(scheme))


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1", "ff02::1", "::ffff:1.1.1.1"],
)
def test_production_policy_denies_nonpublic_and_mapped(monkeypatch, address):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (
                socket.AF_INET6 if ipaddress.ip_address(address).version == 6 else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (address, 443),
            )
        ],
    )
    made = Mock()
    monkeypatch.setattr(socket, "socket", made)
    with pytest.raises(OSError, match="NETWORK_BLOCKED"):
        NetworkPolicy(("source.example",)).connect(("source.example", 443))
    made.assert_not_called()


def test_production_policy_pins_dns_and_rejects_mixed_answers(monkeypatch):
    answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]
    lookup = Mock(return_value=answers)
    sock = Mock()
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(socket, "socket", Mock(return_value=sock))
    policy = NetworkPolicy(("source.example",))
    assert policy.connect(("source.example", 443)) is sock
    sock.connect.assert_called_once_with(("1.1.1.1", 443))
    assert lookup.call_count == 1
    lookup.return_value = answers + [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
    ]
    with pytest.raises(OSError, match="NETWORK_BLOCKED"):
        policy.connect(("source.example", 443))


def test_requests_keys_subtitles_and_response_lease(public_route, tmp_path):
    root, _, _ = public_route
    for name in ("key", "subtitle.vtt"):
        (root / name).write_text("fixture")
    with YoutubeDL({"quiet": True, "proxy": ""}) as d:
        install_transport(d, ["source.example"], tmp_path / "locks", 1)
        response = d.urlopen(Request("http://source.example/key"))
        assert response.read(1) == b"f"
        with pytest.raises(OSError, match="capacity"):
            SlotPool(tmp_path / "locks", 1).acquire(timeout=0)
        response.close()
        with d.urlopen(Request("http://source.example/subtitle.vtt")) as subtitle:
            assert subtitle.read() == b"fixture"
        SlotPool(tmp_path / "locks", 1).acquire(timeout=0).release()
        for url in (
            "http://evil.example/key",
            "http://source.example:9999/sub.vtt",
            "http://user:pass@source.example/file",
        ):
            with pytest.raises(RequestError):
                d.urlopen(Request(url))


def test_real_tls_validates_hostname_and_redirect_is_guarded(tmp_path, monkeypatch):
    openssl = shutil.which("openssl")
    assert openssl, "TLS acceptance requires openssl on the test runner"
    key, cert = tmp_path / "key.pem", tmp_path / "cert.pem"
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-subj",
            "/CN=source.example",
            "-addext",
            "subjectAltName=DNS:source.example",
        ],
        check=True,
        capture_output=True,
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "https://127.0.0.1/private")
            else:
                self.send_response(200)
            self.end_headers()
            self.wfile.write(b"fixture")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_lookup, original_socket = socket.getaddrinfo, socket.socket

    def lookup(host, port, *args, **kwargs):
        if host in ("source.example", "wrong.example"):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", port))]
        return original_lookup(host, port, *args, **kwargs)

    class TestSocket(original_socket):
        def connect(self, target):
            if target[0] == "1.1.1.1":
                target = ("127.0.0.1", server.server_port)
            return super().connect(target)

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(socket, "socket", TestSocket)
    policy = NetworkPolicy(("source.example", "wrong.example", "127.0.0.1"))
    rh = restricted_handler(policy, SlotPool(tmp_path / "locks", 2))
    trusted = ssl.create_default_context(cafile=str(cert))
    monkeypatch.setattr(rh, "_make_sslcontext", lambda self, **kwargs: trusted)
    try:
        with YoutubeDL({"quiet": True, "proxy": ""}) as d:
            d.__dict__["_request_director"] = d.build_request_director([rh])
            with d.urlopen(Request("https://source.example/")) as response:
                assert response.read() == b"fixture"
            with pytest.raises(RequestError):
                d.urlopen(Request("https://wrong.example/"))
            with pytest.raises(Exception) as failure:
                d.urlopen(Request("https://source.example/redirect"))
            assert public_error(failure.value)["code"] == "NETWORK_BLOCKED_ADDRESS"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_ffmpeg_probe_cannot_fetch_nested_network_urls(public_route, tmp_path):
    from idlix_downloader.extractor import IdlixIE

    root, requests, _ = public_route
    (root / "outer.m3u8").write_text("#EXTM3U\nmalicious.ts\n")
    (root / "malicious.ts").write_text(
        "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\nhttp://127.0.0.1/leak\n#EXT-X-ENDLIST\n"
    )
    cfg = config(tmp_path, "http://source.example/outer.m3u8")
    with media_downloader(cfg, tmp_path / "output", Events(lambda event: None)) as d:
        with pytest.raises(Exception, match="Dimensi tidak terbaca"):
            IdlixIE(d)._probe_dimensions(
                {"url": "http://source.example/outer.m3u8"}, "fixture", "http://source.example/"
            )
    assert not any("/leak" in path for path, _, _ in requests)


def test_provider_format_id_is_not_used_as_path_or_expression(public_route, tmp_path, monkeypatch):
    from yt_dlp.extractor.generic import GenericIE

    def extract(_self, _url):
        return {
            "id": "fixture",
            "title": "fixture",
            "formats": [
                {
                    "format_id": "../../escape/best",
                    "url": "http://source.example/direct.mp4",
                    "protocol": "http",
                    "ext": "mp4",
                    "width": 1920,
                    "height": 1080,
                    "vcodec": "h264",
                    "acodec": "aac",
                }
            ],
        }

    monkeypatch.setattr(GenericIE, "_real_extract", extract)
    result = run_job(
        config(tmp_path, "http://source.example/direct.mp4"), Events(lambda event: None)
    )
    assert result["artifacts"][0]["name"] == "media.mp4"
    assert not (tmp_path / "escape").exists()


def test_guarded_native_dash_transfer(public_route, tmp_path):
    root, requests, binary = public_route
    subprocess.run(
        [
            binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(root / "direct.mp4"),
            "-map",
            "0",
            "-c",
            "copy",
            "-f",
            "dash",
            str(root / "dash.mpd"),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    result = run_job(config(tmp_path, "http://source.example/dash.mpd"), Events(lambda event: None))
    assert result["effective_quality"]["quality_class"] == 1080
    assert result["artifacts"]
    assert any(".m4s" in path for path, _, _ in requests)


def test_hls_rotation_across_every_verified_g7_host(public_route, tmp_path, monkeypatch):
    from idlix_downloader.web.config import WebConfig

    root, _, _ = public_route
    rules = WebConfig(tmp_path).allowed_hosts
    domains = [rule for rule in rules if rule.startswith(".") and rule != ".majorplay.net"]
    hosts = ["g7" + domain for domain in domains]
    policy = NetworkPolicy(rules)
    assert all(policy.host_allowed("z42" + domain) for domain in domains)
    assert not policy.host_allowed("z42.unlisted.example")
    assert len(hosts) == 30
    lookup = socket.getaddrinfo

    def mapped(host, port, *args, **kwargs):
        return lookup("source.example" if host in hosts else host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", mapped)
    visited = set()
    validate = NetworkPolicy.validate

    def tracked(self, url):
        result = validate(self, url)
        if result.hostname in hosts:
            visited.add(result.hostname)
        return result

    monkeypatch.setattr(NetworkPolicy, "validate", tracked)
    playlist = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:1", "#EXT-X-MEDIA-SEQUENCE:0"]
    for host in hosts:
        playlist.extend(["#EXT-X-DISCONTINUITY", "#EXTINF:1,", "http://" + host + "/10800.ts"])
    playlist.append("#EXT-X-ENDLIST")
    (root / "rotating-video.m3u8").write_text("\n".join(playlist) + "\n")
    (root / "rotating-master.m3u8").write_text(
        (root / "master.m3u8").read_text().replace("1080.m3u8", "rotating-video.m3u8")
    )
    cfg = config(tmp_path, "http://source.example/rotating-master.m3u8")
    cfg["allowed_hosts"] = ["source.example", *rules]
    result = run_job(cfg, Events(lambda event: None))
    assert visited == set(hosts)
    assert result["effective_quality"]["quality_class"] == 1080
    assert result["artifacts"] and not result.get("partial")


def test_disallowed_port_has_distinct_error():
    policy = NetworkPolicy(("source.example",))
    with pytest.raises(OSError) as failure:
        policy.validate("https://source.example:8443/video.mp4?token=SECRET")
    error = public_error(failure.value)
    assert error["code"] == "NETWORK_BLOCKED_PORT"
    assert "SECRET" not in str(error)
