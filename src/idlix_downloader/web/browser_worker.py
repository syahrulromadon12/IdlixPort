"""Bounded opt-in browser session probe; secret output is private IPC only."""

import json
import os
import sys
import threading
from urllib.parse import urlsplit

from yt_dlp.cookies import extract_cookies_from_browser

from .browser_storage import memory_cookie_reader
from .engine import media_downloader
from .errors import AppError, public_error
from .events import Events
from .inspection import normalize_dimensions
from .quality import choices
from .session_cookies import encode_cookies, usable
from .worker import MAX_MESSAGE, monitor_parent


class SilentLogger:
    def debug(self, *_args, **_kwargs):
        pass

    info = warning = error = debug


def probe(config):
    host = urlsplit(config["payload"]["url"]).hostname
    cookies = config.get("cookies", [])
    if config.get("browser"):
        try:
            with memory_cookie_reader(config["browser"], host):
                jar = extract_cookies_from_browser(
                    config["browser"], profile=config["profile"], logger=SilentLogger()
                )
            cookies = encode_cookies(jar, host)
        except PermissionError as error:
            raise AppError("COOKIE_PERMISSION") from error
        except Exception as error:
            raise AppError("COOKIE_UNAVAILABLE") from error
    config["cookies"] = cookies
    evidence = {}
    result = {"cookies": cookies, "account_verified": False, "media_ready": False, "code": None}
    try:
        from pathlib import Path

        with media_downloader(
            config, Path(config["output"]), Events(lambda event: None)
        ) as downloader:
            downloader.web_session_evidence = evidence
            info = downloader.extract_info(config["payload"]["url"], download=False)
            normalize_dimensions(info, downloader, config["payload"]["url"])
            result["media_ready"] = any(choice["audio_available"] for choice in choices(info))
            result["max_height"] = info.get("session_max_height")
    except Exception as error:
        result["code"] = public_error(error)["code"]
    names = {cookie["name"] for cookie in usable(cookies)}
    if (
        result["code"] == "VERIFICATION_REQUIRED"
        and evidence.get("stage") == "play_info"
        and not evidence.get("account_verified")
        and "refresh_token" in names
        and "access_token" not in names
    ):
        result["code"] = "SESSION_EXPIRED"
    result["stage"] = evidence.get("stage", "page")
    result["account_verified"] = evidence.get("account_verified", False)
    if not result["account_verified"] and result["code"] is None:
        result["code"] = "SESSION_NOT_VERIFIED"
    return result


def main():
    os.umask(0o077)
    try:
        data = sys.stdin.buffer.readline(MAX_MESSAGE + 1)
        if len(data) > MAX_MESSAGE:
            raise AppError("COOKIE_UNAVAILABLE")
        config = json.loads(data)
        if os.name != "nt":
            threading.Thread(
                target=monitor_parent, args=(config["parent_pid"],), daemon=True
            ).start()
        result = probe(config)
    except Exception as error:
        result = {
            "code": public_error(error)["code"],
            "account_verified": False,
            "media_ready": False,
        }
    encoded = json.dumps(result, allow_nan=False)
    if len(encoded.encode()) > MAX_MESSAGE:
        encoded = json.dumps({"code": "COOKIE_UNAVAILABLE"})
    print(encoded, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
