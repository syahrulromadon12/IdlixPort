"""Bounded metadata inspection; no media session URLs leave the worker."""

import math
import re
import subprocess
from urllib.parse import urlsplit

from yt_dlp.extractor.generic import GenericIE
from yt_dlp.networking import Request
from yt_dlp.networking.exceptions import RequestError
from yt_dlp.utils import ExtractorError

from ..downloader import ffmpeg_path
from ..extractor import IdlixIE
from .errors import AppError
from .quality import candidates

MAX_FORMATS = 12
SAMPLE_BYTES = 262144


class LimitedFormats:
    def _limit(self, result):
        if len(result[0]) > MAX_FORMATS:
            raise AppError("PROBE_LIMIT")
        return result

    def _extract_m3u8_formats_and_subtitles(self, *args, **kwargs):
        return self._limit(super()._extract_m3u8_formats_and_subtitles(*args, **kwargs))

    def _extract_mpd_formats_and_subtitles(self, *args, **kwargs):
        return self._limit(super()._extract_mpd_formats_and_subtitles(*args, **kwargs))


class WebIdlixIE(LimitedFormats, IdlixIE):
    def _api_headers(self, endpoint, page_url):
        headers = super()._api_headers(endpoint, page_url)
        source, target = urlsplit(page_url), urlsplit(endpoint)
        # IDLIX's API requires the existing browser access cookie as a Bearer.
        # Never attach it to a provider, CDN, HTTP endpoint or another origin.
        if (
            source.scheme == target.scheme == "https"
            and source.netloc == target.netloc
            and target.path.startswith("/api/")
        ):
            cookie = self._get_cookies(endpoint).get("access_token")
            if cookie and re.fullmatch(r"[A-Za-z0-9._~-]{1,8192}", cookie.value):
                headers["Authorization"] = "Bearer " + cookie.value
        return headers

    def _api(self, endpoint, *args, **kwargs):
        evidence = getattr(self._downloader, "web_session_evidence", None)
        if evidence is not None:
            evidence["stage"] = (
                "play_info"
                if "/play-info/" in endpoint
                else "claim"
                if "/session/claim" in endpoint
                else "metadata"
                if "/api/movies/" in endpoint
                else "provider"
            )
        result = super()._api(endpoint, *args, **kwargs)
        if "/api/watch/play-info/" in endpoint:
            evidence = getattr(self._downloader, "web_session_evidence", None)
            if evidence is not None:
                evidence["account_verified"] = result.get("viewerTier") in {
                    "user",
                    "member",
                    "premium",
                    "authenticated",
                    "registered",
                    "free",
                }
        return result

    @classmethod
    def ie_key(cls):
        return "Idlix"


class WebGenericIE(LimitedFormats, GenericIE):
    @classmethod
    def ie_key(cls):
        return "Generic"


def normalize_dimensions(info, downloader, referer):
    formats = info.get("formats") or ([info] if info.get("url") else [])
    if len(formats) > MAX_FORMATS:
        raise AppError("PROBE_LIMIT")
    separate_audio = any(
        item.get("vcodec") == "none" and item.get("acodec") != "none" for item in formats
    )
    for item in formats:
        if (
            not separate_audio
            and item.get("height")
            and item.get("vcodec") != "none"
            and item.get("acodec") in (None, "none")
            and item.get("protocol") == "m3u8_native"
        ):
            try:
                streams = IdlixIE(downloader)._probe_hls_streams(
                    item, str(info.get("id", "probe")), referer
                )
            except (ExtractorError, RequestError):
                pass
            else:
                audio = re.search(r"^\s*Stream #[^\r\n]*?: Audio: ([\w-]+)", streams, re.M)
                if audio:
                    item["acodec"] = audio[1]
        if item.get("height") or item.get("vcodec") == "none":
            continue
        if item.get("protocol") == "m3u8_native":
            IdlixIE(downloader)._probe_dimensions(item, str(info.get("id", "probe")), referer)
        elif item.get("protocol") in ("http", "https"):
            binary = ffmpeg_path()
            if not binary:
                raise AppError("FFMPEG_MISSING")
            with downloader.urlopen(
                Request(
                    item["url"],
                    headers={"Range": f"bytes=0-{SAMPLE_BYTES - 1}", "Referer": referer},
                )
            ) as response:
                sample = response.read(SAMPLE_BYTES)
            result = subprocess.run(
                [
                    binary,
                    "-hide_banner",
                    "-protocol_whitelist",
                    "pipe",
                    "-format_whitelist",
                    "mov,matroska,webm,mpegts,aac,mp3,ogg,flac",
                    "-i",
                    "pipe:0",
                ],
                input=sample,
                capture_output=True,
                timeout=15,
            )
            text = result.stderr.decode(errors="replace")
            frame = re.search(r"Video:[^\r\n]*?[, ](\d{2,5})x(\d{2,5})(?=[ ,\[])", text)
            if frame:
                item["width"], item["height"] = map(int, frame.groups())
                item["format_note"] = "dimensi dari sampel video"
                if "Audio:" in text and item.get("acodec") is None:
                    item["acodec"] = "unknown"
    return info


def estimates(info):
    result = {}
    duration = info.get("duration")
    for choice, item in candidates(info):
        size = item.get("filesize") or item.get("filesize_approx")
        if size is None and isinstance(duration, (int, float)) and item.get("tbr"):
            size = duration * item["tbr"] * 1000 / 8
        if isinstance(size, (int, float)) and math.isfinite(size) and size > 0:
            # Exact mux size is unknown when audio is separate; add advertised audio estimate.
            if item.get("acodec") in (None, "none"):
                audio = [f for f in info.get("formats", []) if f.get("vcodec") == "none"]
                if audio and isinstance(duration, (int, float)):
                    size += (
                        duration
                        * max((f.get("abr") or f.get("tbr") or 0) for f in audio)
                        * 1000
                        / 8
                    )
            result[choice.choice_id] = int(size)
    return result
