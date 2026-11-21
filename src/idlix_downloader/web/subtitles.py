"""Bounded subtitle fetch and local soft-subtitle remux; never encode video."""

import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from yt_dlp.networking import Request

from ..downloader import ffmpeg_path
from .errors import AppError

MAX_SUBTITLE_BYTES = 2 * 1024 * 1024
LANGUAGES = {
    "id": ("Indonesia", "ind"),
    "en": ("English", "eng"),
    "es": ("Español", "spa"),
    "fr": ("Français", "fra"),
    "de": ("Deutsch", "deu"),
    "ja": ("日本語", "jpn"),
    "ko": ("한국어", "kor"),
    "zh": ("中文", "zho"),
    "pt": ("Português", "por"),
}


def language_code(value):
    value = str(value).lower().replace("_", "-")
    parts = value.split("-", 1)
    parts[0] = {"ind": "id", "eng": "en"}.get(parts[0], parts[0])
    value = "-".join(parts)
    return value if re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})?", value) else None


def tracks(info):
    result = {}
    for language, entries in (info.get("subtitles") or {}).items():
        code = language_code(language)
        if not code:
            continue
        for entry in entries:
            url = entry.get("url", "")
            ext = (entry.get("ext") or Path(urlsplit(url).path).suffix.lstrip(".")).lower()
            if ext in ("srt", "vtt", "ass") and urlsplit(url).scheme in ("http", "https"):
                result.setdefault(code, (entry, ext))
    return result


def catalog(info):
    return [
        {"code": code, "label": LANGUAGES.get(code.split("-")[0], (code,))[0]}
        for code in sorted(tracks(info))
    ]


def selection(mode, language, available):
    if mode == "auto":
        language = (
            "id"
            if "id" in available
            else next((code for code in sorted(available) if code.startswith("id-")), None)
        )
        return ("embed", language) if language else ("off", None)
    return mode, language


def local_ffmpeg(arguments, *, timeout=60):
    try:
        result = subprocess.run(
            [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *arguments],
            capture_output=True,
            timeout=timeout,
        )
        if result.returncode:
            raise AppError("SUBTITLE_FAILED")
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AppError("SUBTITLE_FAILED") from error


def complete(info, downloader, video, output, mode, language, events):
    available = tracks(info)
    mode, language = selection(mode, language, available)
    if mode == "off":
        return [video], {"mode": "off", "language": None}
    if language not in available:
        raise AppError("SUBTITLE_UNAVAILABLE")
    entry, ext = available[language]
    events.phase("subtitles")
    raw = output / ("subtitle-input." + ext)
    normalized = output / ("subtitle." + language + ".srt")
    with downloader.urlopen(
        Request(
            entry["url"],
            headers={**(info.get("http_headers") or {}), **(entry.get("http_headers") or {})},
        )
    ) as response:
        data = response.read(MAX_SUBTITLE_BYTES + 1)
    if not data or len(data) > MAX_SUBTITLE_BYTES:
        raise AppError("SUBTITLE_FAILED")
    raw.write_bytes(data)
    try:
        local_ffmpeg(
            [
                "-protocol_whitelist",
                "file,pipe",
                "-format_whitelist",
                "srt,webvtt,ass",
                "-i",
                str(raw),
                "-map",
                "0:s:0",
                "-vn",
                "-an",
                "-c:s",
                "srt",
                str(normalized),
            ]
        )
    finally:
        raw.unlink(missing_ok=True)
    if not normalized.is_file() or not normalized.stat().st_size:
        raise AppError("SUBTITLE_FAILED")
    if mode == "separate":
        separate = video.with_name(video.stem + "." + language + ".srt")
        normalized.replace(separate)
        return [video, separate], {"mode": mode, "language": language}
    events.phase("merging")
    merged = output / "film.mkv"
    label, iso = LANGUAGES.get(
        language.split("-")[0], (language, language if len(language) == 3 else "und")
    )

    def mux(source):
        local_ffmpeg(
            [
                "-protocol_whitelist",
                "file,pipe",
                "-format_whitelist",
                "mov,matroska,webm",
                "-i",
                str(source),
                "-protocol_whitelist",
                "file,pipe",
                "-format_whitelist",
                "srt",
                "-i",
                str(normalized),
                "-map",
                "0:v:0",
                "-map",
                "0:a",
                "-map",
                "1:s:0",
                "-c",
                "copy",
                "-metadata:s:s:0",
                "language=" + iso,
                "-metadata:s:s:0",
                "title=" + label,
                "-disposition:s:0",
                "default",
                str(merged),
            ],
            timeout=300,
        )

    intermediate = output / "subtitle-remux.mp4"
    try:
        try:
            mux(video)
        except AppError:
            with video.open("rb") as handle:
                prefix = handle.read(377)
            if prefix[::188] != b"GGG":
                raise
            local_ffmpeg(
                [
                    "-protocol_whitelist",
                    "file,pipe",
                    "-f",
                    "mpegts",
                    "-i",
                    str(video),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a",
                    "-c",
                    "copy",
                    str(intermediate),
                ],
                timeout=300,
            )
            mux(intermediate)
        if not merged.is_file() or not merged.stat().st_size:
            raise AppError("SUBTITLE_FAILED")
    except BaseException:
        merged.unlink(missing_ok=True)
        raise
    finally:
        intermediate.unlink(missing_ok=True)
    return [merged], {"mode": mode, "language": language}
