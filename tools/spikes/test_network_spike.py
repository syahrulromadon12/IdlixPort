"""P0 feasibility only; not a production transport or web security guarantee."""

import http.client
import ipaddress
import socket
import urllib.request
from unittest.mock import Mock

import pytest
from yt_dlp import YoutubeDL
from yt_dlp.networking import Request
from yt_dlp.networking._urllib import HTTPHandler, UrllibRH


def pinned_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    """Resolve once, reject any nonpublic answer, then connect to a validated literal."""
    host, port = address
    answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not answers:
        raise OSError("NETWORK_BLOCKED")
    for answer in answers:
        ip = ipaddress.ip_address(answer[4][0])
        if not ip.is_global or ip.is_multicast or getattr(ip, "ipv4_mapped", None):
            raise OSError("NETWORK_BLOCKED")
    family, kind, protocol, _, target = answers[0]
    sock = socket.socket(family, kind, protocol)
    try:
        if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
            sock.settimeout(timeout)
        if source_address:
            sock.bind(source_address)
        sock.connect(target)
        return sock
    except BaseException:
        sock.close()
        raise


def connection_factory(base, *args, **kwargs):
    connection = base(*args, **kwargs)
    connection._create_connection = pinned_connection
    return connection


class PinnedHTTPHandler(HTTPHandler):
    def http_open(self, req):
        return self.do_open(
            lambda *a, **kw: connection_factory(http.client.HTTPConnection, *a, **kw), req
        )

    def https_open(self, req):
        return self.do_open(
            lambda *a, **kw: connection_factory(http.client.HTTPSConnection, *a, **kw),
            req,
            context=self._context,
        )


class SpikeRH(UrllibRH):
    _SUPPORTED_URL_SCHEMES = ("http", "https")
    _SUPPORTED_PROXY_SCHEMES = ()
    RH_NAME = "P0 spike"

    def _create_instance(self, proxies, cookiejar, legacy_ssl_support=None):
        if any(proxies.values()):
            raise OSError("PROXY_BLOCKED")
        from yt_dlp.networking._urllib import RedirectHandler

        handlers = (
            urllib.request.ProxyHandler({}),
            PinnedHTTPHandler(context=self._make_sslcontext()),
            urllib.request.HTTPCookieProcessor(cookiejar),
            RedirectHandler(),
            urllib.request.UnknownHandler(),
            urllib.request.HTTPDefaultErrorHandler(),
            urllib.request.HTTPErrorProcessor(),
        )
        opener = urllib.request.OpenerDirector()
        for handler in handlers:
            opener.add_handler(handler)
        return opener


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "ff02::1",
        "::ffff:127.0.0.1",
    ],
)
def test_blocks_nonpublic_before_socket(monkeypatch, address):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 80))],
    )
    create = Mock()
    monkeypatch.setattr(socket, "socket", create)
    with pytest.raises(OSError, match="NETWORK_BLOCKED"):
        pinned_connection(("source.example", 80))
    create.assert_not_called()


def test_mixed_dns_answer_is_rejected(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
        ],
    )
    with pytest.raises(OSError, match="NETWORK_BLOCKED"):
        pinned_connection(("source.example", 80))


def test_dns_is_not_resolved_again_before_connect(monkeypatch):
    lookup = Mock(
        side_effect=[
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 80))],
            AssertionError("second resolution"),
        ]
    )
    sock = Mock()
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(socket, "socket", Mock(return_value=sock))
    assert pinned_connection(("source.example", 80), 2) is sock
    sock.connect.assert_called_once_with(("1.1.1.1", 80))
    lookup.assert_called_once()


def test_https_preserves_hostname_for_tls(monkeypatch):
    sock = Mock()
    monkeypatch.setattr(__name__ + ".pinned_connection", Mock(return_value=sock))
    context = Mock()
    connection = connection_factory(http.client.HTTPSConnection, "source.example", context=context)
    connection.connect()
    context.wrap_socket.assert_called_once_with(sock, server_hostname="source.example")


@pytest.mark.parametrize(
    "path",
    [
        "/api/movie",
        "/redeem",
        "/master.m3u8",
        "/variant.m3u8",
        "/segment.ts",
        "/key",
        "/subtitle.vtt",
    ],
)
def test_yt_dlp_urlopen_hits_guard(monkeypatch, path):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))],
    )
    with YoutubeDL({"quiet": True, "proxy": ""}) as downloader:
        downloader.__dict__["_request_director"] = downloader.build_request_director([SpikeRH])
        with pytest.raises(Exception, match="NETWORK_BLOCKED"):
            downloader.urlopen(Request("http://source.example" + path))


def test_redirect_cannot_connect_to_private_address(monkeypatch):
    # Follow an actual HTTP redirect through the yt-dlp handler, using a public
    # DNS answer routed to a loopback fixture only by the test's socket adapter.
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1/private")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    real_lookup = socket.getaddrinfo
    real_socket = socket.socket

    def lookup(host, port, **kwargs):
        if host == "source.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", port))]
        return real_lookup(host, port, **kwargs)

    class TestSocket(real_socket):
        def connect(self, target):
            if target[0] == "1.1.1.1":
                target = ("127.0.0.1", server.server_port)
            return super().connect(target)

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(socket, "socket", TestSocket)
    try:
        with YoutubeDL({"quiet": True, "proxy": ""}) as downloader:
            downloader.__dict__["_request_director"] = downloader.build_request_director([SpikeRH])
            with pytest.raises(Exception, match="NETWORK_BLOCKED"):
                downloader.urlopen(Request("http://source.example/redirect"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
