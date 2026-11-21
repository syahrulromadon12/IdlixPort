"""Adapter IDLIX Next.js/Pentos dan markup Dooplay.

Alur Next.js diverifikasi pada satu film dengan sesi tamu dan transfer terbatas.
Dooplay diuji melalui fixture. Domain, API, dan provider dapat berubah.
"""

import json
import re
import subprocess
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.networking import Request
from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor
from yt_dlp.utils import ExtractorError, smuggle_url, update_url_query


class PlayerParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.servers = []
        self.iframes = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "li" and all(
            attributes.get(name) for name in ("data-post", "data-nume", "data-type")
        ):
            if attributes["data-type"] not in {"movie", "tv"}:
                return
            if attributes["data-nume"] == "trailer":
                return
            self.servers.append(
                {
                    name.removeprefix("data-"): attributes[name]
                    for name in ("data-post", "data-nume", "data-type")
                }
            )
        if tag == "iframe":
            source = attributes.get("src") or attributes.get("data-src")
            if source:
                self.iframes.append(source)


def player_url(value, page_url):
    """Terima URL HTTP(S) langsung atau satu iframe dari respons player."""
    if not isinstance(value, str):
        raise ExtractorError("Respons player tidak berisi URL yang didukung.", expected=True)
    value = unescape(value).strip()
    if value.startswith("<"):
        parser = PlayerParser()
        parser.feed(value)
        if len(parser.iframes) != 1:
            raise ExtractorError("Respons player harus berisi satu iframe.", expected=True)
        value = parser.iframes[0]
    result = urljoin(page_url, value)
    parts = urlsplit(result)
    if not value or parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ExtractorError("URL player harus menggunakan HTTP atau HTTPS.", expected=True)
    if result.rstrip("/") == page_url.rstrip("/"):
        raise ExtractorError("Player mengarah kembali ke halaman yang sama.", expected=True)
    return result


class DooplayIE(InfoExtractor):
    IE_NAME = "dooplay"
    # Pola luas memungkinkan mirror tanpa nama IDLIX dipilih lewat --page-adapter.
    _VALID_URL = r"https?://[^/?#]+/(?P<id>[^?#]+)"

    def _real_extract(self, url):
        video_id = self._match_id(url).strip("/").rsplit("/", 1)[-1]
        webpage = self._download_webpage(url, video_id)
        return self._extract_dooplay_page(url, video_id, webpage)

    def _extract_dooplay_page(self, url, video_id, webpage):
        parser = PlayerParser()
        parser.feed(webpage)
        if not parser.servers:
            # GenericIE menangani video HTML5 dan embed penyedia yang didukung yt-dlp.
            return self.url_result(url, ie="Generic")

        server = int(self._configuration_arg("server", ["1"])[0])
        if not 1 <= server <= len(parser.servers):
            raise ExtractorError(
                f"Server {server} tidak tersedia. Pilih 1 sampai {len(parser.servers)}.",
                expected=True,
            )
        selected = parser.servers[server - 1]
        payload = self._download_json(
            urljoin(url, "/wp-admin/admin-ajax.php"),
            video_id,
            note=f"Membaca player server {server}/{len(parser.servers)}",
            data=urlencode({"action": "doo_player_ajax", **selected}).encode(),
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": url,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        if not isinstance(payload, dict):
            raise ExtractorError("Respons server player tidak didukung.", expected=True)
        target = player_url(payload.get("embed_url"), url)
        return {
            "_type": "url_transparent",
            "url": smuggle_url(target, {"referer": url}),
            "id": video_id,
            "title": self._og_search_title(webpage, default=None)
            or self._html_extract_title(webpage, default=video_id),
        }


class IdlixIE(DooplayIE):
    IE_NAME = "idlix"

    def _probe_hls_streams(self, item, video_id, page_url):
        """Baca maksimal 256 KiB dari satu segmen/init untuk metadata frame."""
        ffmpeg = FFmpegPostProcessor(self._downloader)
        if not ffmpeg.available:
            raise ExtractorError(
                "Manifest tidak mencantumkan resolusi. FFmpeg diperlukan untuk memeriksanya.",
                expected=True,
            )
        headers = {"Referer": page_url}
        playlist = self._download_webpage(item["url"], video_id, headers=headers)
        if re.search(r"#EXT-X-KEY:.*METHOD=(?!NONE(?:,|$))", playlist):
            raise ExtractorError(
                "Resolusi playlist terenkripsi tidak dapat diperiksa.", expected=True
            )
        init = re.search(r'#EXT-X-MAP:.*?URI="([^"]+)"', playlist)
        source = (
            init[1]
            if init
            else next(
                (
                    line.strip()
                    for line in playlist.splitlines()
                    if line.strip() and not line.startswith("#")
                ),
                None,
            )
        )
        if not source:
            raise ExtractorError("Playlist tidak menyediakan segmen video.", expected=True)
        target = urljoin(item["url"], source)
        query = item.get("extra_param_to_segment_url")
        if query:
            target = update_url_query(target, parse_qs(query))
        headers["Range"] = "bytes=0-262143"
        self.to_screen("Memeriksa dimensi video dari sampel maksimal 256 KiB")
        try:
            with self._downloader.urlopen(Request(target, headers=headers)) as response:
                sample = response.read(262144)
            restricted = self._downloader.params.get("_idlixport_local_media")
            input_args = (
                [
                    "-protocol_whitelist",
                    "pipe",
                    "-format_whitelist",
                    "mov,matroska,webm,mpegts,aac,mp3,ogg,flac",
                ]
                if restricted
                else []
            )
            result = subprocess.run(
                [ffmpeg.executable, "-hide_banner", *input_args, "-i", "pipe:0"],
                input=sample,
                capture_output=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ExtractorError("Pemeriksaan dimensi video gagal.", expected=True) from error
        return result.stderr.decode(errors="replace")

    def _probe_dimensions(self, item, video_id, page_url):
        streams = self._probe_hls_streams(item, video_id, page_url)
        match = re.search(
            r"Video:[^\r\n]*?[, ](\d{2,5})x(\d{2,5})(?=[ ,\[])",
            streams,
        )
        if not match:
            raise ExtractorError(
                "Dimensi tidak terbaca dari sampel video. Format belum dapat dipilih dengan aman.",
                expected=True,
            )
        item["width"], item["height"] = map(int, match.groups())
        item["format_note"] = "dimensi dari sampel video"
        # Film sinema dapat terpotong vertikal; lebar tetap menentukan kelas akses.
        item["session_required_height"] = max(item["height"], (item["width"] * 9 + 15) // 16)

    def series_catalog(self, url, slug, season=None):
        endpoint = f"/api/series/{quote(slug, safe='')}"
        if season is not None:
            endpoint += f"/season/{season}"
        return self._api(urljoin(url, endpoint), slug, url)

    def _real_extract(self, url):
        episode = re.fullmatch(r"/series/([\w-]+)/season/(\d+)/episode/(\d+)/?", urlsplit(url).path)
        if episode:
            slug, season, number = episode.groups()
            catalog = self.series_catalog(url, slug, season)
            metadata = next(
                (
                    item
                    for item in (catalog.get("season") or {}).get("episodes", [])
                    if item.get("episodeNumber") == int(number) and item.get("hasVideo")
                ),
                None,
            )
            if not metadata or not metadata.get("id"):
                raise ExtractorError("Episode belum tersedia untuk diputar.", expected=True)
            series = (catalog.get("series") or {}).get("title") or slug
            title = f"{series} S{season}E{number}"
            return self._extract_next_video(url, title, str(metadata["id"]), "episode")
        video_id = self._match_id(url).strip("/").rsplit("/", 1)[-1]
        webpage = self._download_webpage(url, video_id)
        if "/_next/static/" in webpage and urlsplit(url).path.startswith("/movie/"):
            return self._extract_next_movie(url, video_id)
        return self._extract_dooplay_page(url, video_id, webpage)

    def _api_headers(self, endpoint, page_url):
        return {"Referer": page_url, "Content-Type": "application/json"}

    def _api(self, endpoint, video_id, page_url, *, body=None):
        headers = self._api_headers(endpoint, page_url)
        value = self._download_json(
            endpoint,
            video_id,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            expected_status=(400, 401, 403, 409, 422, 429),
        )
        if not isinstance(value, dict):
            raise ExtractorError("Respons API IDLIX tidak didukung.", expected=True)
        if value.get("error"):
            raise ExtractorError(
                "API IDLIX menolak sesi. Periksa akses akun, masa berlaku cookies, "
                "atau buka player di browser jika situs meminta verifikasi.",
                expected=True,
            )
        return value

    def _extract_next_movie(self, url, slug):
        metadata = self._api(urljoin(url, f"/api/movies/{quote(slug)}"), slug, url)
        video_id = str(metadata.get("id") or "")
        if not video_id or not metadata.get("hasVideo"):
            raise ExtractorError(
                "Film ini tidak menyediakan video yang dapat diputar.", expected=True
            )
        return self._extract_next_video(url, metadata.get("title") or slug, video_id, "movie")

    def _extract_next_video(self, url, title, video_id, kind):
        session = self._api(
            urljoin(url, f"/api/watch/play-info/{kind}/{quote(video_id, safe='')}"), video_id, url
        )
        if session.get("verification"):
            raise ExtractorError(
                "Situs meminta verifikasi pemutaran di browser; adapter belum mendukung alur ini.",
                expected=True,
            )
        if session.get("kind") == "gate":
            try:
                delay = max(0, (session["unlockAt"] - session["serverNow"]) / 1000) + 0.5
            except (KeyError, TypeError) as error:
                raise ExtractorError("Waktu tunggu sesi tidak valid.", expected=True) from error
            if delay > 60 or not session.get("gateToken"):
                raise ExtractorError(
                    "Sesi meminta waktu tunggu atau verifikasi tambahan.", expected=True
                )
            self._sleep(delay, video_id, msg_template="Menunggu sesi situs %(timeout)s detik")
            session = self._api(
                urljoin(url, "/api/watch/session/claim"),
                video_id,
                url,
                body={"gateToken": session["gateToken"]},
            )
        if session.get("kind") != "pentos" or not session.get("claim"):
            raise ExtractorError("Jenis sesi player IDLIX belum didukung.", expected=True)
        redeem_value = session.get("redeemUrl")
        if isinstance(redeem_value, str):
            redeem_value = unescape(redeem_value).strip()
            relative = urlsplit(redeem_value)
            # Current IDLIX returns this route relative to its API base, not
            # the page root. Keep other absolute/relative provider URLs intact.
            if (
                not relative.scheme
                and not relative.netloc
                and relative.path == "/watch/session/redeem"
            ):
                redeem_value = "/api" + redeem_value
        redeem_url = player_url(redeem_value, url)
        redeem_parts = urlsplit(redeem_url)
        host = (redeem_parts.hostname or "").lower()
        same_origin = (
            redeem_parts.scheme == urlsplit(url).scheme
            and redeem_parts.netloc == urlsplit(url).netloc
        )
        if (
            redeem_parts.scheme != "https"
            or redeem_parts.username is not None
            or redeem_parts.password is not None
            or not (same_origin or host == "majorplay.net" or host.endswith(".majorplay.net"))
        ):
            raise ExtractorError("Provider sesi berubah; adapter perlu diperbarui.", expected=True)
        stream = self._api(
            redeem_url, video_id, url, body={"claim": session["claim"], "mode": "browser"}
        )
        if stream.get("code") != "ok":
            raise ExtractorError("Provider tidak memberikan URL media yang valid.", expected=True)
        media_url = player_url(stream.get("url"), url)
        formats, subtitles = self._extract_m3u8_formats_and_subtitles(
            media_url, video_id, headers={"Referer": url}
        )
        signed_query = urlsplit(media_url).query
        # Manifest provider saat ini memakai NAME="720p" tanpa RESOLUTION.
        # Label adalah klaim sumber, bukan hasil pengukuran frame video.
        for item in formats:
            # Playlist relatif provider tidak menyertakan query otorisasi master.
            # Teruskan parameter sesi asli, termasuk ke segmen dan key HLS.
            if signed_query and urlsplit(item["url"]).netloc == urlsplit(media_url).netloc:
                item["url"] = update_url_query(item["url"], parse_qs(signed_query))
                item["extra_param_to_segment_url"] = signed_query
                item["extra_param_to_key_url"] = signed_query
            label = re.fullmatch(r"(\d{3,4})p", item.get("format_id") or "")
            if not item.get("height") and label:
                item["height"] = int(label[1])
                item["format_note"] = "resolusi dari label manifest"
            if not item.get("height") and item.get("vcodec") != "none":
                self._probe_dimensions(item, video_id, url)
        limit = session.get("maxHeight")
        if not isinstance(limit, int) or limit <= 0:
            raise ExtractorError("Batas resolusi sesi tidak diketahui.", expected=True)
        formats = [
            item
            for item in formats
            if item.get("vcodec") == "none"
            or 0 < (item.get("session_required_height") or item.get("height") or 0) <= limit
        ]
        if not any(item.get("vcodec") != "none" for item in formats):
            raise ExtractorError(
                f"Tidak ada varian video yang memenuhi batas sesi {limit}p.", expected=True
            )
        for item in stream.get("subtitles") or []:
            if not isinstance(item, dict) or not item.get("path"):
                continue
            language = {"ind": "id", "eng": "en"}.get(item.get("lang"), item.get("lang") or "und")
            subtitles.setdefault(language, []).append(
                {
                    "url": player_url(item["path"], url),
                    "name": item.get("label"),
                    "http_headers": {"Referer": url},
                }
            )
        return {
            "id": video_id,
            "title": title,
            "formats": formats,
            "subtitles": subtitles,
            "duration": session.get("durationSec"),
            "http_headers": {"Referer": url},
            "session_max_height": limit,
            "session_viewer_tier": session.get("viewerTier"),
        }

    @classmethod
    def suitable(cls, url):
        parts = urlsplit(url)
        return (
            "idlix" in (parts.hostname or "").lower()
            and not parts.path.lower().endswith((".m3u8", ".mpd", ".mp4", ".webm", ".mkv"))
            and super().suitable(url)
        )
