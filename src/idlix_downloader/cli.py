"""Antarmuka terminal berbahasa Indonesia."""

import argparse
import shutil
import sys
from pathlib import Path

from yt_dlp.utils import YoutubeDLError
from yt_dlp.version import __version__ as yt_dlp_version

from . import __version__
from .downloader import (
    QUALITIES,
    Settings,
    download_video,
    download_videos,
    ffmpeg_path,
    inspect_video,
    validate_url,
)


def parser():
    root = argparse.ArgumentParser(
        prog="idlixport",
        description="IdlixPort — periksa dan unduh video IDLIX atau URL media HTTP(S).",
    )
    root.add_argument("--version", action="version", version=f"IdlixPort {__version__}")
    commands = root.add_subparsers(dest="command", required=True)
    web = commands.add_parser("web", help="Jalankan fondasi API web lokal.")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--data-dir", type=Path)
    web.add_argument("--allow-host", action="append", default=None)
    web.add_argument("--download-dir", action="append", type=Path, default=[])
    doctor = commands.add_parser("doctor", help="Periksa Python, yt-dlp, dan FFmpeg.")
    doctor.add_argument("--ffmpeg-location", help="Path binary atau direktori FFmpeg.")
    for command, help_text in (
        ("info", "Tampilkan format yang tersedia tanpa mengunduh video."),
        ("download", "Unduh satu atau banyak film; jalankan ulang untuk resume."),
    ):
        sub = commands.add_parser(command, help=help_text)
        if command == "download":
            sub.add_argument("urls", nargs="*", help="URL halaman film, player, atau media.")
            sub.add_argument(
                "--batch-file", type=Path, help="File UTF-8 berisi satu URL film per baris."
            )
        else:
            sub.add_argument("url", help="URL halaman film, player, MP4, M3U8, atau MPD.")
        sub.add_argument(
            "--server", type=int, default=1, help="Urutan server Dooplay (default: 1)."
        )
        sub.add_argument("--referer", help="URL halaman asal jika provider membutuhkannya.")
        session = sub.add_mutually_exclusive_group()
        session.add_argument("--cookies", type=Path, help="File cookies Netscape milik pengguna.")
        session.add_argument(
            "--cookies-from-browser",
            choices=("safari", "chrome", "firefox", "edge", "brave"),
            help="Gunakan sesi browser lokal pilihan pengguna.",
        )
        sub.add_argument(
            "--page-adapter", action="store_true", help="Paksa adapter Dooplay untuk domain mirror."
        )
        if command == "download":
            sub.add_argument(
                "-q",
                "--quality",
                choices=QUALITIES,
                default="1080",
                help="Batas tinggi video, atau best tanpa batas (default: 1080).",
            )
            sub.add_argument("-o", "--output", type=Path, default=Path("downloads"))
            sub.add_argument(
                "-N",
                "--connections",
                type=int,
                default=8,
                help="Segmen HLS/DASH paralel, 1–16 (default: 8).",
            )
            sub.add_argument("--ffmpeg-location", help="Path binary atau direktori FFmpeg.")
            sub.add_argument(
                "--subtitles",
                action="store_true",
                help="Simpan subtitle id/en yang disediakan extractor.",
            )
    return root


def collect_urls(urls, batch_file=None):
    """Validasi seluruh input sebelum akses jaringan; hilangkan URL duplikat persis."""
    candidates = [(url, "argumen URL") for url in urls]
    if batch_file:
        for number, line in enumerate(
            batch_file.expanduser().read_text(encoding="utf-8-sig").splitlines(), 1
        ):
            value = line.strip()
            if value and not value.startswith("#"):
                candidates.append((value, f"{batch_file}, baris {number}"))
    unique = {}
    for url, location in candidates:
        try:
            validate_url(url)
        except ValueError as error:
            raise ValueError(f"{location}: {error}") from error
        unique.setdefault(url, None)
    if not unique:
        raise ValueError("Masukkan URL film atau --batch-file dengan daftar URL.")
    return list(unique)


def show_info(info):
    print(f"Judul: {info.get('title') or info.get('id') or '-'}")
    if info.get("session_max_height"):
        print(
            f"Batas sesi: {info['session_max_height']}p "
            f"({info.get('session_viewer_tier') or 'tidak diketahui'})"
        )
    print("Format tersedia (video/audio):")
    formats = info.get("formats") or ([info] if info.get("url") else [])
    if not formats:
        print("  Tidak ada format yang dapat diunduh.")
        return
    for item in formats:
        if item.get("vcodec") == "none":
            resolution = "audio"
        elif item.get("height"):
            resolution = f"{item['height']}p"
            if item.get("format_note") == "resolusi dari label manifest":
                resolution += " (label sumber)"
            elif item.get("format_note") == "dimensi dari sampel video":
                resolution = f"{item['width']}×{item['height']} (dimensi video)"
        else:
            resolution = "resolusi tidak diketahui"
        size = item.get("filesize") or item.get("filesize_approx")
        size_text = f"{size / 1024**2:.1f} MiB" if size else "ukuran tidak diketahui"
        drm = " | DRM" if item.get("has_drm") else ""
        print(
            f"  {item.get('format_id', '-')} | {resolution} | "
            f"{item.get('ext', '-')} | {size_text}{drm}"
        )


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "web":
        try:
            import uvicorn

            from .web.app import create_app
            from .web.config import WebConfig, default_data_dir
        except ImportError:
            print(
                "Instal dependensi web: python -m pip install -e '.[web,portable]'", file=sys.stderr
            )
            return 1
        try:
            kwargs = {
                "data_dir": args.data_dir or default_data_dir(),
                "port": args.port,
                "download_dirs": tuple(args.download_dir),
            }
            if args.allow_host:
                kwargs["allowed_hosts"] = tuple(args.allow_host)
            config = WebConfig(**kwargs)
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 1
        from .web.locking import FileLock

        instance = FileLock(config.data_dir / "instance.lock")
        if instance.path.exists() and not instance.acquire():
            print(
                "Instance IdlixPort lain masih aktif pada folder data ini. "
                "Gunakan instance tersebut atau hentikan sebelum menjalankan lagi.",
                file=sys.stderr,
            )
            return 1
        instance.release()
        uvicorn.run(
            create_app(config),
            host="127.0.0.1",
            port=config.port,
            proxy_headers=False,
            access_log=False,
            log_level="warning",
        )
        return 0
    if args.command == "doctor":
        binary = ffmpeg_path(args.ffmpeg_location)
        print(f"Python: {sys.version.split()[0]}")
        print(f"yt-dlp: {yt_dlp_version}")
        print(f"FFmpeg: {binary or 'belum ditemukan'}")
        print(f"FFprobe: {shutil.which('ffprobe') or 'opsional, belum ditemukan'}")
        return 0 if binary else 1
    try:
        settings = Settings(
            quality=getattr(args, "quality", "1080"),
            output=getattr(args, "output", Path("downloads")),
            connections=getattr(args, "connections", 8),
            server=args.server,
            referer=args.referer,
            cookies=args.cookies,
            cookies_from_browser=args.cookies_from_browser,
            page_adapter=args.page_adapter,
            ffmpeg_location=getattr(args, "ffmpeg_location", None),
            subtitles=getattr(args, "subtitles", False),
        )
        if args.command == "info":
            show_info(inspect_video(args.url, settings))
        else:
            urls = collect_urls(args.urls, args.batch_file)
            if len(urls) == 1:
                download_video(urls[0], settings)
            else:
                failures = 0
                for number, result in enumerate(download_videos(urls, settings), 1):
                    if result.error is not None:
                        failures += 1
                        print(f"[{number}/{len(urls)}] Gagal: {result.error}", file=sys.stderr)
                    else:
                        print(f"[{number}/{len(urls)}] Selesai/tersedia: {result.title or 'film'}")
                print(f"Hasil batch: {len(urls) - failures} selesai/tersedia, {failures} gagal.")
                print(f"Folder hasil: {settings.output.expanduser().resolve()}")
                return 1 if failures else 0
            print(f"Selesai. Folder hasil: {settings.output.expanduser().resolve()}")
        return 0
    except KeyboardInterrupt:
        print("\nDihentikan. Jalankan perintah yang sama untuk melanjutkan.", file=sys.stderr)
        return 130
    except (YoutubeDLError, ValueError, OSError) as error:
        print(f"Gagal: {error}", file=sys.stderr)
        message = str(error).lower()
        if "403" in message or "429" in message:
            print(
                "Server menolak akses. Coba lagi nanti atau gunakan URL player/stream "
                "yang bisa diakses, beserta --referer jika diperlukan.",
                file=sys.stderr,
            )
        elif "requested format is not available" in message:
            print(
                "Jalankan 'idlixport info URL' untuk melihat resolusi. Jika tinggi tidak "
                "diketahui, --quality best mengizinkan format tersebut tanpa batas resolusi.",
                file=sys.stderr,
            )
        return 1
