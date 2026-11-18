"""Restricted yt-dlp transport. No alternate handlers, proxy or DNS re-resolution."""

import http.client
import ipaddress
import socket
import threading
import urllib.request
from dataclasses import dataclass
from functools import partial
from urllib.parse import urlsplit

from yt_dlp.networking import Response
from yt_dlp.networking._urllib import HTTPHandler, RedirectHandler, UrllibRH
from yt_dlp.networking.exceptions import HTTPError, RequestError

from ..downloader import validate_url
from .locking import SlotPool


@dataclass(frozen=True)
class NetworkPolicy:
    hosts: tuple[str, ...]

    def host_allowed(self, host):
        host = host.encode("idna").decode().lower().rstrip(".")
        return any(
            host == rule.lower().rstrip(".")
            or (rule.startswith(".") and host.endswith(rule.lower().rstrip(".")))
            for rule in self.hosts
        )

    def validate(self, url):
        validate_url(url)
        parts = urlsplit(url)
        if not self.host_allowed(parts.hostname) or parts.port not in (None, 80, 443):
            raise OSError("NETWORK_BLOCKED")
        return parts

    def connect(self, address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
        host, port = address
        if not self.host_allowed(host) or port not in (80, 443):
            raise OSError("NETWORK_BLOCKED")
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not answers:
            raise OSError("NETWORK_BLOCKED")
        for answer in answers:
            ip = ipaddress.ip_address(answer[4][0])
            if not ip.is_global or ip.is_multicast or getattr(ip, "ipv4_mapped", None):
                raise OSError("NETWORK_BLOCKED")
        last = None
        for family, kind, protocol, _, target in answers:
            sock = socket.socket(family, kind, protocol)
            try:
                if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                    sock.settimeout(timeout)
                if source_address:
                    sock.bind(source_address)
                sock.connect(target)
                return sock
            except OSError as error:
                last = error
                sock.close()
        raise last


class GuardedHTTPHandler(HTTPHandler):
    def __init__(self, policy, **kwargs):
        super().__init__(**kwargs)
        self.policy = policy

    def connection(self, base, *args, **kwargs):
        connection = base(*args, **kwargs)
        connection._create_connection = self.policy.connect
        return connection

    def http_open(self, request):
        self.policy.validate(request.full_url)
        return self.do_open(partial(self.connection, http.client.HTTPConnection), request)

    def https_open(self, request):
        self.policy.validate(request.full_url)
        return self.do_open(
            partial(self.connection, http.client.HTTPSConnection), request, context=self._context
        )


class ReadBudget:
    def __init__(self):
        self.enabled = True
        self.remaining = 6 * 1024 * 1024
        self.lock = threading.Lock()

    def disable(self):
        with self.lock:
            self.enabled = False


class LeasedResponse(Response):
    def __init__(self, response, lease, budget=None):
        self.lease, self.budget, self.consumed = lease, budget, 0
        super().__init__(
            response,
            response.url,
            dict(response.headers),
            response.status,
            response.reason,
            response.extensions,
        )

    def read(self, amt=None):
        try:
            if self.budget is not None:
                with self.budget.lock:
                    if self.budget.enabled:
                        available = min(self.budget.remaining, 2 * 1024 * 1024 - self.consumed)
                        limit = min(amt, available + 1) if amt is not None else available + 1
                        data = super().read(limit)
                        if len(data) > available:
                            raise RequestError("PROBE_LIMIT")
                        self.consumed += len(data)
                        self.budget.remaining -= len(data)
                    else:
                        data = super().read(amt)
            else:
                data = super().read(amt)
            if not data and amt != 0:
                self.close()
            return data
        except BaseException:
            self.close()
            raise

    def close(self):
        try:
            super().close()
        finally:
            self.lease.release()


def restricted_handler(policy, pool, budget=None):
    """Bind immutable operator policy to a single handler class per worker."""

    class RestrictedRH(UrllibRH):
        _SUPPORTED_URL_SCHEMES = ("http", "https")
        _SUPPORTED_PROXY_SCHEMES = ()
        RH_NAME = "IdlixPort restricted HTTP"

        def _create_instance(self, proxies, cookiejar, legacy_ssl_support=None):
            if any(proxies.values()):
                raise OSError("NETWORK_BLOCKED")
            opener = urllib.request.OpenerDirector()
            for handler in (
                urllib.request.ProxyHandler({}),
                GuardedHTTPHandler(policy, context=self._make_sslcontext()),
                urllib.request.HTTPCookieProcessor(cookiejar),
                RedirectHandler(),
                urllib.request.UnknownHandler(),
                urllib.request.HTTPDefaultErrorHandler(),
                urllib.request.HTTPErrorProcessor(),
            ):
                opener.add_handler(handler)
            return opener

        def _send(self, request):
            try:
                policy.validate(request.url)
                lease = pool.acquire()
            except (OSError, ValueError) as error:
                raise RequestError("NETWORK_BLOCKED") from error
            try:
                return LeasedResponse(super()._send(request), lease, budget)
            except HTTPError as error:
                error.response = LeasedResponse(error.response, lease, budget)
                raise
            except BaseException:
                lease.release()
                raise

    return RestrictedRH


def install_transport(downloader, hosts, lock_dir, slots, budget=None):
    policy = NetworkPolicy(tuple(hosts))
    handler = restricted_handler(policy, SlotPool(lock_dir, slots), budget)
    downloader.__dict__["_request_director"] = downloader.build_request_director([handler])
    return policy
