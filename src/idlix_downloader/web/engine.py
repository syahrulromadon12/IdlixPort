"""Worker-only media adapter; untrusted URLs never reach an external downloader."""

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
from .errors import AppError
from .events import SafeLogger
from .files import ensure_space
from .inspection import WebGenericIE, WebIdlixIE, estimates, normalize_dimensions
from .network import ReadBudget, install_transport
from .quality import PROTOCOLS, choices, select
from .resume import prepare_resume
from .session_cookies import decode_cookies


def reject_external(*_args, **_kwargs):
    raise AppError("NETWORK_BLOCKED")


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


def entrypoint(url):
    if not IdlixIE.suitable(url) and not urlsplit(url).path.lower().endswith(
        (".mp4", ".m3u8", ".mpd", ".webm", ".mkv")
    ):
        raise AppError("NETWORK_BLOCKED")


def run_job(config, events):
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
    with media_downloader(config, output, events) as downloader:
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
    artifacts = []
    for path in output.iterdir():
        if path.suffix not in (".mp4", ".mkv", ".webm") or path.is_symlink():
            continue
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
        artifacts.append({"name": path.name, "size_bytes": path.stat().st_size})
    if not artifacts:
        raise AppError("WORKER_FAILED")
    return {"effective_quality": effective, "artifacts": artifacts, "resumed": resumed}
