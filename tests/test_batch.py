from contextlib import contextmanager

import pytest
from yt_dlp.utils import DownloadError

from idlix_downloader.cli import collect_urls, main
from idlix_downloader.downloader import DownloadResult, Settings, download_videos

FIRST = "https://idlix.example/movie/first"
SECOND = "https://idlix.example/movie/second"
THIRD = "https://idlix.example/movie/third"


def test_batch_file_handles_bom_comments_and_duplicates(tmp_path):
    path = tmp_path / "films.txt"
    path.write_text(
        f"# Film pilihan\n\n {SECOND} \n{FIRST}\n{SECOND}\n{THIRD}#player\n", encoding="utf-8-sig"
    )
    assert collect_urls([FIRST], path) == [FIRST, SECOND, f"{THIRD}#player"]


def test_invalid_batch_line_reports_location_before_any_download(tmp_path, monkeypatch, capsys):
    path = tmp_path / "films.txt"
    path.write_text(f"{FIRST}\nfile:///private/file\n")
    calls = []
    monkeypatch.setattr("idlix_downloader.cli.download_video", lambda *args: calls.append(args))
    monkeypatch.setattr("idlix_downloader.cli.download_videos", lambda *args: calls.append(args))
    assert main(["download", "--batch-file", str(path)]) == 1
    assert "baris 2" in capsys.readouterr().err
    assert calls == []


@pytest.mark.parametrize("lines", ["", "# Belum ada film\n\n"])
def test_empty_batch_is_rejected(tmp_path, lines):
    path = tmp_path / "films.txt"
    path.write_text(lines)
    with pytest.raises(ValueError, match="Masukkan URL"):
        collect_urls([], path)


def test_download_requires_url_or_batch(capsys):
    assert main(["download"]) == 1
    assert "Masukkan URL" in capsys.readouterr().err


def test_batch_reuses_session_and_continues_after_failure(monkeypatch):
    sessions = []
    extracted = []

    class Downloader:
        def extract_info(self, url, **_kwargs):
            extracted.append(url)
            if url == SECOND:
                raise DownloadError("Film kedua gagal.")
            return {
                "id": url.rsplit("/", 1)[-1],
                "title": url,
                "height": 720 if url == FIRST else 1080,
            }

        def process_ie_result(self, info, **_kwargs):
            return info

    @contextmanager
    def shared(settings):
        sessions.append(settings)
        yield Downloader()

    monkeypatch.setattr("idlix_downloader.downloader.create_downloader", shared)
    monkeypatch.setattr("idlix_downloader.downloader.require_ffmpeg", lambda *_args: None)
    results = list(download_videos([FIRST, SECOND, THIRD], Settings(quality="best")))
    assert len(sessions) == 1
    assert extracted == [FIRST, SECOND, THIRD]
    assert [result.error is None for result in results] == [True, False, True]
    assert results[2].title == THIRD


def test_batch_ctrl_c_stops_remaining_films(monkeypatch):
    @contextmanager
    def shared(_settings):
        yield object()

    def stop(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr("idlix_downloader.downloader.create_downloader", shared)
    monkeypatch.setattr("idlix_downloader.downloader.require_ffmpeg", lambda *_args: None)
    monkeypatch.setattr("idlix_downloader.downloader._download_one", stop)
    with pytest.raises(KeyboardInterrupt):
        list(download_videos([FIRST, SECOND], Settings()))


def test_cli_batch_reports_partial_failure_and_shares_login(monkeypatch, capsys):
    calls = []

    def batch(urls, settings):
        calls.append((urls, settings))
        yield DownloadResult(FIRST, title="Film pertama")
        yield DownloadResult(SECOND, error="Provider gagal")

    monkeypatch.setattr("idlix_downloader.cli.download_videos", batch)
    assert (
        main(["download", FIRST, SECOND, "--cookies-from-browser", "safari", "--quality", "best"])
        == 1
    )
    assert calls[0][0] == [FIRST, SECOND]
    assert calls[0][1].cookies_from_browser == "safari"
    assert calls[0][1].quality == "best"
    output = capsys.readouterr()
    assert "1 selesai/tersedia, 1 gagal" in output.out
    assert "[2/2] Gagal: Provider gagal" in output.err


def test_cli_single_url_remains_compatible(monkeypatch):
    calls = []
    monkeypatch.setattr("idlix_downloader.cli.download_video", lambda *args: calls.append(args))
    assert main(["download", FIRST]) == 0
    assert calls[0][0] == FIRST
