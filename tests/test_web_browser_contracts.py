"""Browser paths, cookie scope and account evidence remain server-owned."""

import json
from http.cookiejar import CookieJar
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from idlix_downloader.extractor import IdlixIE
from idlix_downloader.web.browser_profiles import Profile, discover_profiles
from idlix_downloader.web.browser_worker import probe
from idlix_downloader.web.inspection import WebIdlixIE
from idlix_downloader.web.session_cookies import decode_cookies, encode_cookies


def cookie(domain, *, expires=None):
    return next(
        decode_cookies(
            [
                dict(
                    name="auth",
                    value="SYNTHETIC",
                    domain=domain,
                    domain_specified=domain.startswith("."),
                    domain_initial_dot=domain.startswith("."),
                    path="/",
                    path_specified=True,
                    secure=True,
                    expires=expires,
                    discard=True,
                )
            ]
        )
    )


def test_cookie_export_only_matches_source_and_preserves_secure_flags():
    jar = CookieJar()
    for value in (
        cookie(".idlixku.com"),
        cookie(".google.com"),
        cookie("idlixku.com"),
        cookie("other.idlixku.com"),
        cookie("z3.idlixku.com", expires=1),
    ):
        jar.set_cookie(value)
    values = encode_cookies(jar, "z3.idlixku.com")
    assert len(values) == 1 and values[0]["domain"] == ".idlixku.com"
    rebuilt = next(decode_cookies(values))
    assert rebuilt.secure and rebuilt.domain_specified and rebuilt.path == "/"


@pytest.mark.parametrize(
    "tier,maximum,verified",
    [
        ("guest", 1080, False),
        ("unknown", 1080, False),
        (None, 1080, False),
        ("member", 720, True),
        ("premium", 1080, True),
        ("user", 1080, True),
    ],
)
def test_account_proof_is_explicit_tier_never_maximum(monkeypatch, tier, maximum, verified):
    monkeypatch.setattr(
        IdlixIE, "_api", lambda self, *a, **kw: dict(viewerTier=tier, maxHeight=maximum)
    )
    evidence = {}
    extractor = WebIdlixIE()
    extractor._downloader = SimpleNamespace(web_session_evidence=evidence)
    extractor._api("https://z3.idlixku.com/api/watch/play-info/movie/fixture")
    assert evidence["account_verified"] is verified


def test_chrome_login_uses_literal_profile_and_url_without_shell(tmp_path, monkeypatch):
    folder = tmp_path / "Default"
    folder.mkdir()
    cookie_file = folder / "Cookies"
    cookie_file.touch()
    profile = Profile("chrome", folder, cookie_file, application="Google Chrome")
    launch = Mock()
    monkeypatch.setattr("idlix_downloader.web.browser_profiles.subprocess.Popen", launch)
    monkeypatch.setattr("idlix_downloader.web.browser_profiles.sys.platform", "darwin")
    assert profile.login("https://z3.idlixku.com/")
    assert launch.call_args.args[0] == [
        "/usr/bin/open",
        "-a",
        "Google Chrome",
        "--args",
        "--profile-directory=Default",
        "https://z3.idlixku.com/",
    ]
    assert not launch.call_args.kwargs.get("shell", False)
    assert not Profile("safari", folder, cookie_file).login("https://z3.idlixku.com/")
    assert not Profile("firefox", folder, cookie_file).login("https://z3.idlixku.com/")
    assert "path" not in profile.public() and str(folder) not in json.dumps(profile.public())


def test_catalog_does_not_follow_symlink_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr("idlix_downloader.web.browser_profiles.Path.home", lambda: tmp_path)
    monkeypatch.setattr("idlix_downloader.web.browser_profiles.sys.platform", "darwin")
    root = tmp_path / "Library/Application Support/Google/Chrome"
    root.mkdir(parents=True)
    real = root / "Profile 1"
    real.mkdir()
    (real / "Cookies").touch()
    (root / "Default").symlink_to(real, target_is_directory=True)
    catalog = discover_profiles()
    assert len(catalog) == 1
    assert next(iter(catalog.values())).path == real
    original = type(real).is_file

    def metadata_denied(path):
        if path.name == "Cookies.binarycookies":
            raise PermissionError("Synthetic Safari metadata denial")
        return original(path)

    monkeypatch.setattr(type(real), "is_file", metadata_denied)
    assert len(discover_profiles()) == 1  # Safari denial must not disable guest startup.


def test_browser_reader_permission_does_not_leak_exception(monkeypatch, tmp_path):
    def denied(*a, **kw):
        raise PermissionError("PRIVATE_PATH_AND_SECRET")

    monkeypatch.setattr("idlix_downloader.web.browser_worker.extract_cookies_from_browser", denied)
    from idlix_downloader.web.errors import AppError

    with pytest.raises(AppError, match="COOKIE_PERMISSION"):
        probe(
            {
                "browser": "safari",
                "profile": str(tmp_path / "Cookies.binarycookies"),
                "payload": {"url": "https://z3.idlixku.com/movie/fixture"},
            }
        )


def test_verified_cdn_is_exact_and_still_rejects_private_dns(tmp_path, monkeypatch):
    import socket

    from idlix_downloader.web.config import WebConfig
    from idlix_downloader.web.network import NetworkPolicy

    policy = NetworkPolicy(WebConfig(tmp_path).allowed_hosts)
    assert (
        policy.validate("https://g2.akademivo.website/video.m3u8").hostname
        == "g2.akademivo.website"
    )
    with pytest.raises(OSError, match="NETWORK_BLOCKED"):
        policy.validate("https://g3.akademivo.website/video.m3u8")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(OSError, match="NETWORK_BLOCKED"):
        policy.connect(("g2.akademivo.website", 443))


def test_chromium_fingerprint_ignores_unrelated_sites(tmp_path):
    import sqlite3

    cookie_file = tmp_path / "Cookies"
    with sqlite3.connect(cookie_file) as db:
        db.execute(
            "CREATE TABLE cookies (host_key,name,path,expires_utc,is_secure,encrypted_value,value)"
        )
        db.executemany(
            "INSERT INTO cookies VALUES(?,?,?,?,?,?,?)",
            [
                (".idlixku.com", "auth", "/", 0, 1, b"source-first", ""),
                (".google.com", "auth", "/", 0, 1, b"other-first", ""),
            ],
        )
    profile = Profile("chrome", tmp_path, cookie_file)
    first = profile.fingerprint("z3.idlixku.com")
    with sqlite3.connect(cookie_file) as db:
        db.execute(
            "UPDATE cookies SET encrypted_value=? WHERE host_key=?", (b"other-new", ".google.com")
        )
    assert profile.fingerprint("z3.idlixku.com") == first
    with sqlite3.connect(cookie_file) as db:
        db.execute(
            "UPDATE cookies SET encrypted_value=? WHERE host_key=?", (b"source-new", ".idlixku.com")
        )
    assert profile.fingerprint("z3.idlixku.com") != first


@pytest.mark.parametrize(
    "browser,table,column",
    [
        ("chrome", "cookies", "host_key"),
        ("firefox", "moz_cookies", "host"),
    ],
)
def test_browser_database_snapshot_is_memory_only_and_source_scoped(
    tmp_path, browser, table, column
):
    import sqlite3

    from yt_dlp import cookies as cookie_module

    from idlix_downloader.web.browser_storage import memory_cookie_reader

    original = cookie_module._open_database_copy
    source = tmp_path / "BrowserCookies"
    empty = tmp_path / "temporary"
    empty.mkdir()
    with sqlite3.connect(source) as db:
        db.execute(f"CREATE TABLE {table} ({column},name,value)")
        db.executemany(
            f"INSERT INTO {table} VALUES(?,?,?)",
            [
                (".idlixku.com", "auth", "SOURCE_SYNTHETIC"),
                (".google.com", "auth", "UNRELATED_SYNTHETIC"),
            ],
        )
    with memory_cookie_reader(browser, "z3.idlixku.com"):
        cursor = cookie_module._open_database_copy(str(source), str(empty))
        try:
            assert cursor.execute(f"SELECT value FROM {table}").fetchall() == [
                ("SOURCE_SYNTHETIC",)
            ]
            assert cursor.execute("PRAGMA database_list").fetchall()[0][2] == ""
            assert list(empty.iterdir()) == []
        finally:
            cursor.connection.close()
    assert cookie_module._open_database_copy is original
    with sqlite3.connect(source) as db:
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 2
