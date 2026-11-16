"""Kebijakan download; pengambilan media diserahkan ke yt-dlp."""

import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from yt_dlp import YoutubeDL
from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor
from yt_dlp.utils import YoutubeDLError

from .extractor import DooplayIE, IdlixIE

QUALITIES = ("best", "2160", "1440", "1080", "720", "480")


@dataclass(frozen=True)
class Settings:
    quality: str = "1080"
    output: Path = Path("downloads")
    connections: int = 8
    server: int = 1
    referer: str | None = None
    cookies: Path | None = None
    cookies_from_browser: str | None = None
    ffmpeg_location: str | None = None
    subtitles: bool = False
    page_adapter: bool = False

    def __post_init__(self):
        if self.quality not in QUALITIES:
            raise ValueError(f"Kualitas harus salah satu: {', '.join(QUALITIES)}")
        if not 1 <= self.connections <= 16:
            raise ValueError("Jumlah koneksi harus antara 1 dan 16.")
        if self.server < 1:
            raise ValueError("Nomor server dimulai dari 1.")
        if self.cookies and not self.cookies.expanduser().is_file():
            raise ValueError("File cookies tidak ditemukan.")
        if self.cookies and self.cookies_from_browser:
            raise ValueError("Pilih file cookies atau cookies browser, bukan keduanya.")
        if self.referer:
            validate_url(self.referer)


@dataclass(frozen=True)
class DownloadResult:
    url: str
    title: str | None = None
    error: str | None = None


def validate_url(url):
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        _ = parts.port
    except ValueError as error:
        raise ValueError("URL tidak valid.") from error
    if (
        parts.scheme not in {"http", "https"}
        or not hostname
        or parts.username is not None
        or parts.password is not None
        or any(character.isspace() or ord(character) < 32 for character in url)
    ):
        raise ValueError("Gunakan URL HTTP(S) lengkap tanpa kredensial atau spasi.")
    return url


def format_selector(quality):
    if quality == "best":
        return "bv*+ba/b"
    # Batas keras: metadata tinggi yang tidak diketahui tidak dianggap <= batas.
    return f"bv*[height<={quality}]+ba/b[height<={quality}]"


def ffmpeg_path(location=None):
    if location:
        path = Path(location).expanduser()
        if path.is_dir():
            return shutil.which("ffmpeg", path=str(path))
        return shutil.which(str(path))
    binary = shutil.which("ffmpeg")
    if binary:
        return binary
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        return shutil.which(get_ffmpeg_exe())
    except (ImportError, RuntimeError):
        return None


def options(settings, *, inspect=False):
    result = {
        "format": format_selector(settings.quality),
        "format_sort": ["res", "fps", "br"],
        "paths": {"home": str(settings.output.expanduser())},
        "outtmpl": {"default": "%(title).150B [%(id).60B] [%(height)sp].%(ext)s"},
        "windowsfilenames": True,
        "noplaylist": True,
        "extract_flat": "in_playlist",
        "concurrent_fragment_downloads": settings.connections,
        "retries": 5,
        "fragment_retries": 5,
        "extractor_retries": 2,
        "socket_timeout": 20,
        "skip_unavailable_fragments": False,
        "continuedl": True,
        "overwrites": False,
        "merge_output_format": "mkv",
        "writedescription": False,
        "writeinfojson": False,
        "writesubtitles": settings.subtitles,
        "subtitleslangs": ["id", "en"],
        "extractor_args": {
            "idlix": {"server": [str(settings.server)]},
            "dooplay": {"server": [str(settings.server)]},
        },
        "cachedir": False,
        "ignoreerrors": False,
    }
    binary = ffmpeg_path(settings.ffmpeg_location)
    if binary:
        result["ffmpeg_location"] = binary
    if settings.cookies:
        result["cookiefile"] = str(settings.cookies.expanduser())
    if settings.cookies_from_browser:
        result["cookiesfrombrowser"] = (settings.cookies_from_browser,)
    if settings.referer:
        result["http_headers"] = {"Referer": settings.referer}
    if inspect:
        # Daftar format tetap dapat dibaca jika batas kualitas tidak tersedia.
        result.update({"format": "best", "ignore_no_formats_error": True, "quiet": True})
    return result


@contextmanager
def create_downloader(settings, *, inspect=False):
    configured = options(settings, inspect=inspect)
    # Seleksi downloader FFmpeg dalam versi yt-dlp yang dipatok juga membaca
    # ContextVar ini, termasuk ketika binary portable tidak berada pada PATH.
    token = FFmpegPostProcessor._ffmpeg_location.set(configured.get("ffmpeg_location"))
    try:
        with YoutubeDL(configured, auto_init=False) as downloader:
            downloader.add_info_extractor(IdlixIE())
            downloader.add_default_info_extractors()
            # Adapter mirror hanya dipilih untuk halaman awal secara eksplisit.
            if settings.page_adapter:
                downloader.add_info_extractor(DooplayIE())
            yield downloader
    finally:
        FFmpegPostProcessor._ffmpeg_location.reset(token)


def inspect_video(url, settings):
    validate_url(url)
    with create_downloader(settings, inspect=True) as downloader:
        info = downloader.extract_info(
            url, download=False, ie_key="Dooplay" if settings.page_adapter else None
        )
    return single_video(info)


def single_video(info):
    if not info:
        raise ValueError("Tidak ada video yang ditemukan.")
    if info.get("_type") in {"playlist", "multi_video"}:
        raise ValueError("Halaman berisi playlist. Gunakan URL player satu film langsung.")
    if info.get("is_live"):
        raise ValueError("Live stream tidak didukung. Gunakan video film yang sudah selesai.")
    return info


def download_video(url, settings):
    validate_url(url)
    require_ffmpeg(settings)
    with create_downloader(settings) as downloader:
        return _download_one(downloader, url, settings)


def require_ffmpeg(settings):
    if not ffmpeg_path(settings.ffmpeg_location):
        raise ValueError(
            "FFmpeg belum ditemukan. Instal FFmpeg atau gunakan --ffmpeg-location PATH."
        )


def _download_one(downloader, url, settings):
    info = single_video(
        downloader.extract_info(
            url, download=False, ie_key="Dooplay" if settings.page_adapter else None
        )
    )
    return downloader.process_ie_result(info, download=True)


def download_videos(urls, settings):
    """Unduh berurutan dengan satu sesi; kegagalan per film tidak menghentikan batch."""
    urls = list(urls)
    if not urls:
        raise ValueError("Daftar URL film kosong.")
    for url in urls:
        validate_url(url)
    require_ffmpeg(settings)
    with create_downloader(settings) as downloader:
        for url in urls:
            try:
                info = _download_one(downloader, url, settings)
            except (YoutubeDLError, ValueError, OSError) as error:
                yield DownloadResult(url=url, error=str(error))
            else:
                yield DownloadResult(url=url, title=info.get("title"))
