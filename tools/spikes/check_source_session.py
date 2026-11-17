"""Opt-in metadata probe. Never print cookie values, signed URLs or raw errors."""

import argparse
import json
import re
from urllib.parse import urlsplit

from idlix_downloader import downloader as core
from idlix_downloader.extractor import IdlixIE


class SilentLogger:
    def debug(self, *args, **kwargs):
        pass

    info = warning = error = debug


def probe(url, browser):
    original_json = IdlixIE._download_json
    had_json_override = "_download_json" in IdlixIE.__dict__
    original_api = IdlixIE._api
    trace = {"stage": "page_or_generic", "session": {}}

    def traced_api(self, endpoint, *args, **kwargs):
        path = urlsplit(endpoint).path
        trace["stage"] = (
            "movie_metadata"
            if "/api/movies/" in path
            else "play_info"
            if "/play-info/" in path
            else "claim"
            if "/session/claim" in path
            else "provider"
        )
        result = original_api(self, endpoint, *args, **kwargs)
        for key in ("maxHeight", "viewerTier", "kind"):
            value = result.get(key)
            if isinstance(value, int) or value in (
                "guest",
                "user",
                "member",
                "premium",
                "authenticated",
                "gate",
                "pentos",
            ):
                trace["session"][key] = value
        if "verification" in result:
            trace["session"]["requires_browser_verification"] = bool(result["verification"])
        if isinstance(result.get("redeemUrl"), str):
            target = urlsplit(result["redeemUrl"])
            trace["session"]["provider_host"] = target.hostname
            trace["session"]["provider_scheme"] = target.scheme
        if isinstance(result.get("viewerTier"), str):
            trace["session"]["tier_present"] = True
            trace["session"]["tier_is_guest"] = result["viewerTier"] == "guest"
        return result

    def traced_json(self, *args, **kwargs):
        result = original_json(self, *args, **kwargs)
        if isinstance(result, dict) and result.get("error"):
            detail = " ".join(
                str(result.get(key, "")) for key in ("error", "message", "code", "detail", "reason")
            ).lower()
            trace["api_error_flags"] = [
                flag
                for flag in (
                    "rate",
                    "limit",
                    "origin",
                    "csrf",
                    "token",
                    "expired",
                    "captcha",
                    "verify",
                    "wait",
                    "gate",
                    "login",
                    "session",
                    "auth",
                    "forbidden",
                    "referer",
                    "device",
                    "cookie",
                    "claim",
                    "early",
                    "ready",
                    "invalid",
                    "pending",
                    "challenge",
                    "human",
                    "denied",
                    "blocked",
                    "missing",
                    "access",
                )
                if flag in detail
            ]
        return result

    original_options = core.options
    original_manifest = IdlixIE._extract_m3u8_formats_and_subtitles

    def quiet_options(settings, **kwargs):
        options = original_options(settings, **kwargs)
        options.update(
            logger=SilentLogger(),
            quiet=True,
            no_warnings=True,
            socket_timeout=10,
            retries=0,
            extractor_retries=0,
        )
        return options

    def bounded_manifest(self, *args, **kwargs):
        formats, subtitles = original_manifest(self, *args, **kwargs)
        if len(formats) > 12:
            raise ValueError("PROBE_VARIANT_LIMIT")
        return formats, subtitles

    IdlixIE._download_json = traced_json
    IdlixIE._api = traced_api
    core.options = quiet_options
    IdlixIE._extract_m3u8_formats_and_subtitles = bounded_manifest
    try:
        info = core.inspect_video(url, core.Settings(cookies_from_browser=browser))
        tier = info.get("session_viewer_tier")
        known_tier = (
            "guest"
            if tier == "guest"
            else "non_guest"
            if isinstance(tier, str) and tier
            else "unknown"
        )
        return {
            "mode": browser or "guest",
            "result": "metadata_received",
            "viewer_tier": known_tier,
            "max_height": info.get("session_max_height"),
            "heights": sorted({f["height"] for f in info.get("formats", []) if f.get("height")}),
            "account_verification": "not_proven"
            if known_tier in ("guest", "unknown")
            else "source_reports_non_guest_tier",
            "trace": trace,
        }
    except Exception as error:
        message = str(error).lower()
        return {
            "mode": browser or "guest",
            "result": "failed",
            "http_status": re.findall(r"http error (\d{3})", message),
            "permission_error": any(
                word in message for word in ("permission", "operation not permitted")
            ),
            "connection_error": any(
                word in message for word in ("connect", "resolve", "timed out")
            ),
            "error_type": type(error).__name__,
            "trace": trace,
            "reason_flags": [
                flag
                for flag in (
                    "unsupported",
                    "no video",
                    "no formats",
                    "403",
                    "404",
                    "json",
                    "api idlix",
                    "provider",
                    "sesi",
                    "cookie",
                    "claim",
                    "early",
                    "ready",
                    "invalid",
                    "pending",
                    "challenge",
                    "human",
                    "denied",
                    "blocked",
                    "missing",
                    "access",
                    "decrypt",
                    "certificate",
                )
                if flag in message
            ],
        }
    finally:
        if had_json_override:
            IdlixIE._download_json = original_json
        else:
            del IdlixIE._download_json
        IdlixIE._api = original_api
        core.options = original_options
        IdlixIE._extract_m3u8_formats_and_subtitles = original_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--browser", choices=("safari", "chrome", "firefox", "edge", "brave"))
    parser.add_argument("--consent-browser-read", action="store_true")
    args = parser.parse_args()
    if len(args.urls) > 3:
        parser.error("Maximum three source pages per probe run.")
    if args.browser and not args.consent_browser_read:
        parser.error("Browser reading requires --consent-browser-read.")
    for url in args.urls:
        core.validate_url(url)
        if "idlix" not in (urlsplit(url).hostname or "").lower():
            parser.error("This spike accepts IDLIX page hosts only.")
    # This standalone, single-threaded spike restores all hooks after each call.
    for index, url in enumerate(args.urls, 1):
        for browser in (None, args.browser) if args.browser else (None,):
            print(json.dumps({"sample": index, **probe(url, browser)}), flush=True)


if __name__ == "__main__":
    main()
