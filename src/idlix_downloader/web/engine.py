"""Worker-only media adapter; untrusted URLs never reach an external downloader."""

import hashlib
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from yt_dlp import YoutubeDL
from yt_dlp.downloader.external import FFmpegFD
from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor

from ..downloader import Settings, ffmpeg_path, options, single_video
from ..extractor import IdlixIE
from . import subtitles
from .errors import AppError, public_error
from .events import SafeLogger
from .files import ensure_space
from .inspection import WebGenericIE, WebIdlixIE, estimates, normalize_dimensions
from .network import ReadBudget, install_transport
from .quality import PROTOCOLS, choices, select
from .resume import prepare_resume
from .session_cookies import decode_cookies


def reject_external(*_args, **_kwargs):
    raise AppError("NETWORK_BLOCKED_EXTERNAL")


class WebYoutubeDL(YoutubeDL):
    def dl(self, name, info, subtitle=False, test=False):
        if any(protocol not in PROTOCOLS for protocol in info.get("protocol", "").split("+")):
            raise AppError("NETWORK_BLOCKED")
        return super().dl(name, info, subtitle=subtitle, test=test)


@contextmanager
def media_downloader(config, output, events):
    settings = Settings(output=output, connections=2)
    configured = options(
        settings,
        progress_hook=events.progress,
        postprocessor_hook=events.postprocess,
        logger=SafeLogger(events),
    )
    configured.update(
        quiet=True,
        no_warnings=True,
        noprogress=True,
        proxy="",
        external_downloader="native",
        hls_prefer_native=True,
        format="bv*+ba/b",
        ignore_no_formats_error=True,
        outtmpl={"default": "media.%(ext)s"},
        writesubtitles=False,
        retries=2,
        fragment_retries=2,
        skip_unavailable_fragments=False,
        extractor_retries=0,
        fixup="never",
        _idlixport_local_media=True,
        postprocessor_args={
            "merger+ffmpeg_i": [
                "-protocol_whitelist",
                "file,pipe",
                "-format_whitelist",
                "mov,matroska,webm,mpegts,aac,mp3,ogg,flac",
            ]
        },
    )
    binary = ffmpeg_path()
    token = FFmpegPostProcessor._ffmpeg_location.set(binary)
    # This context is used only in one isolated worker. It also catches HlsFD's
    # internal FFmpeg fallback, which bypasses YoutubeDL.dl on unsupported HLS.
    original = FFmpegFD.real_download
    FFmpegFD.real_download = reject_external
    try:
        with WebYoutubeDL(configured, auto_init=False) as downloader:
            downloader.add_info_extractor(WebIdlixIE())
            downloader.add_info_extractor(WebGenericIE())
            for cookie in decode_cookies(config.get("cookies", [])):
                downloader.cookiejar.set_cookie(cookie)
            downloader.web_budget = ReadBudget()
            install_transport(
                downloader,
                config["allowed_hosts"],
                config["lock_dir"],
                config["network_slots"],
                budget=downloader.web_budget,
            )
            yield downloader
    except Exception:
        if config.get("require_account") and getattr(events, "session_expired", False):
            raise AppError("SESSION_EXPIRED") from None
        raise
    finally:
        FFmpegFD.real_download = original
        FFmpegPostProcessor._ffmpeg_location.reset(token)


def series_slug(url):
    match = re.fullmatch(r"/series/([\w-]{1,120})/?", urlsplit(url).path)
    return match[1] if match and IdlixIE.suitable(url) else None


def inspect_series(downloader, url, slug):
    extractor = WebIdlixIE(downloader)
    catalog = extractor.series_catalog(url, slug)
    episodes = []
    for season in (catalog.get("seasons") or [])[:12]:
        number = season.get("seasonNumber")
        if type(number) is not int or not 0 <= number <= 999:
            continue
        data = catalog.get("defaultSeason") or {}
        if data.get("seasonNumber") != number:
            data = extractor.series_catalog(url, slug, number).get("season") or {}
        for episode in data.get("episodes") or []:
            index = episode.get("episodeNumber")
            if type(index) is int and 0 < index <= 9999 and episode.get("hasVideo"):
                episodes.append({"season": number, "episode": index})
                if len(episodes) == 200:
                    break
        if len(episodes) == 200:
            break
    return {
        "title": re.sub(r"https?://\S+", "[URL]", str(catalog.get("title") or slug))[:200],
        "choices": [],
        "series_url": urlsplit(url)._replace(query="", fragment="").geturl().rstrip("/"),
        "episodes": episodes,
    }


def entrypoint(url):
    if not IdlixIE.suitable(url) and not urlsplit(url).path.lower().endswith(
        (".mp4", ".m3u8", ".mpd", ".webm", ".mkv")
    ):
        raise AppError("NETWORK_BLOCKED")


def _fresh_job(config, events):
    payload = config["payload"]
    url = payload["url"]
    entrypoint(url)
    output = Path(config["output"])
    if output.is_symlink() or output.resolve() != output:
        raise AppError("OUTPUT_UNWRITABLE")
    if config["kind"] == "download":
        output.mkdir(mode=0o700, parents=True, exist_ok=True)
        ensure_space(output, payload.get("estimated_bytes"))
    events.phase("extracting")
    slug = series_slug(url)
    if slug and config["kind"] != "inspect":
        raise AppError("INVALID_INPUT")
    with media_downloader(config, output, events) as downloader:
        if slug:
            return inspect_series(downloader, url, slug)
        if config.get("require_account"):
            downloader.web_session_evidence = {}
        info = single_video(downloader.extract_info(url, download=False))
        if config.get("require_account") and not downloader.web_session_evidence.get(
            "account_verified"
        ):
            raise AppError("SESSION_EXPIRED")
        normalize_dimensions(info, downloader, url)
        available = choices(info)
        if config["kind"] == "inspect":
            return {
                "title": re.sub(r"https?://\S+", "[URL]", str(info.get("title", "")))[:200],
                "choices": available,
                "session_max_height": info.get("session_max_height"),
                "duration": info.get("duration"),
                "estimates": estimates(info),
                "subtitles": subtitles.catalog(info),
            }
        effective, _selector, formats = select(
            info, payload.get("policy", "max_1080"), payload.get("choice")
        )
        if any(f.get("protocol") not in PROTOCOLS for f in formats):
            raise AppError("NETWORK_BLOCKED")

        formats = [{**item, "format_id": f"web-{index}"} for index, item in enumerate(formats)]

        def selected(_context):
            if len(formats) == 1:
                yield formats[0]
            else:
                yield {
                    **formats[0],
                    "format_id": "+".join(f["format_id"] for f in formats),
                    "protocol": "+".join(f["protocol"] for f in formats),
                    "acodec": formats[1].get("acodec") or "unknown",
                    "requested_formats": formats,
                }

        resumed = prepare_resume(downloader, formats, effective, output, config.get("resume_from"))
        downloader.web_budget.disable()
        downloader.params["format"] = selected
        downloader.format_selector = selected
        if not ffmpeg_path():
            raise AppError("FFMPEG_MISSING")
        downloader.process_ie_result(info, download=True)
    events.phase("validating")
    videos = [path for path in output.iterdir() if path.suffix in (".mp4", ".mkv", ".webm")]
    if len(videos) != 1:
        raise AppError("WORKER_FAILED")
    return finalize(config, events, videos[0], effective, info, resumed)


def identity(info):
    return hashlib.sha256(str(info.get("id", "")).encode()).hexdigest()


def validate_video(path, effective):
    if path.is_symlink() or not path.is_file():
        raise AppError("WORKER_FAILED")
    check = subprocess.run(
        [
            ffmpeg_path(),
            "-hide_banner",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            "mov,matroska,webm,mpegts,aac,mp3,ogg,flac",
            "-i",
            str(path),
        ],
        capture_output=True,
        timeout=15,
    )
    text = check.stderr.decode(errors="replace")
    if "Video:" not in text or "Audio:" not in text:
        raise AppError("WORKER_FAILED")
    frame = re.search(r"Video:[^\r\n]*?[, ](\d{2,5})x(\d{2,5})(?=[ ,\[])", text)
    if not frame:
        raise AppError("QUALITY_CHANGED")
    width, height = map(int, frame.groups())
    if effective["evidence"] != "label" and (
        height != effective["height"] or (effective["width"] and width != effective["width"])
    ):
        raise AppError("QUALITY_CHANGED")
    return text


def finalize(config, events, video, effective, info, resumed=False, source_error=None):
    validate_video(video, effective)
    output = Path(config["output"])
    payload = config["payload"]
    paths = [video]
    mode = payload.get("subtitle_mode", "off")
    language = payload.get("subtitle_language")
    result = {
        "title": re.sub(
            r"https?://\S+", "[URL]", str(info.get("title", payload.get("title", "Film")))
        )[:200],
        "effective_quality": effective,
        "resumed": resumed,
        "source_identity": config.get("source_identity") or identity(info),
    }
    try:
        if source_error:
            raise AppError(source_error)
        with media_downloader(config, output, events) as downloader:
            downloader.web_budget.disable()
            paths, status = subtitles.complete(
                info, downloader, video, output, mode, language, events
            )
        if status["mode"] == "embed":
            text = validate_video(paths[0], effective)
            if "Subtitle:" not in text:
                raise AppError("SUBTITLE_FAILED")
        result["subtitle_status"] = status
    except Exception as error:
        code = error.code if isinstance(error, AppError) else public_error(error)["code"]
        if code == "SOURCE_UNAVAILABLE":
            code = "SUBTITLE_FAILED"
        paths = [video]
        (output / "film.mkv").unlink(missing_ok=True)
        result.update(
            partial=True,
            subtitle_error=code,
            subtitle_status={"mode": mode, "language": language, "failed": True},
        )
    else:
        if result["subtitle_status"]["mode"] == "embed":
            video.unlink()
            for path in output.glob("subtitle.*.srt"):
                path.unlink()
    events.phase("validating")
    result["artifacts"] = [{"name": path.name, "size_bytes": path.stat().st_size} for path in paths]
    return result


def run_job(config, events):
    if not config.get("reuse_video"):
        return _fresh_job(config, events)
    video = Path(config["output"]) / config["reuse_video"]
    effective = config["reuse_quality"]
    validate_video(video, effective)
    info, error = {}, None
    try:
        events.phase("extracting")
        with media_downloader(config, video.parent, events) as downloader:
            if config.get("require_account"):
                downloader.web_session_evidence = {}
            info = single_video(downloader.extract_info(config["payload"]["url"], download=False))
            if config.get("require_account") and not downloader.web_session_evidence.get(
                "account_verified"
            ):
                raise AppError("SESSION_EXPIRED")
            if identity(info) != config["source_identity"]:
                raise AppError("SUBTITLE_UNAVAILABLE")
    except Exception as failure:
        error = public_error(failure)["code"]
    return finalize(config, events, video, effective, info, source_error=error)
