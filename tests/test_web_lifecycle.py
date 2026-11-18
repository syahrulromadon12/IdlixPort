"""P2 expiry, destination and bounded inspection contracts without live sources."""

import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from yt_dlp.networking.common import Response
from yt_dlp.networking.exceptions import RequestError

pytest.importorskip("pydantic")

from idlix_downloader.web.config import WebConfig
from idlix_downloader.web.errors import AppError
from idlix_downloader.web.files import Artifacts, Destinations, open_local
from idlix_downloader.web.inspection import SAMPLE_BYTES, LimitedFormats, normalize_dimensions
from idlix_downloader.web.network import LeasedResponse, ReadBudget
from idlix_downloader.web.quality import choices
from idlix_downloader.web.repository import Repository
from idlix_downloader.web.schemas import DownloadRequest
from idlix_downloader.web.service import Service


@pytest.fixture
def service(tmp_path):
    config = WebConfig(tmp_path / "data")
    repository = Repository(config.data_dir)
    destinations = Destinations(config.data_dir)
    return Service(repository, config, destinations, Artifacts(repository, destinations))


def inspection(service):
    repo = service.repository
    job = repo.enqueue("inspect", {"url": "http://source.example/master.m3u8"})
    assert repo.claim("inspect")["id"] == job
    metadata = {
        "title": "Synthetic",
        "choices": choices(
            {
                "formats": [
                    {
                        "format_id": "video",
                        "height": 1080,
                        "width": 1920,
                        "vcodec": "avc1",
                        "acodec": "aac",
                    }
                ]
            }
        ),
        "estimates": {},
    }
    repo.finish(job, "completed", result=metadata)
    return job


def expire(repo, job):
    with repo.connection() as db:
        db.execute("UPDATE inspections SET expires=1 WHERE job_id=?", (job,))


def test_queued_snapshot_survives_expiry_and_replay(service):
    job = inspection(service)
    request = DownloadRequest(inspection_id=job, policy="max_1080")
    download = service.download(request, "click")
    expire(service.repository, job)
    assert service.snapshot(job)["expired"] is True
    assert service.download(request, "click") == download
    with pytest.raises(AppError, match="INSPECTION_EXPIRED"):
        service.download(request, "new-click")
    claimed = service.repository.claim("download")
    assert claimed["id"] == download and claimed["payload"]["policy"] == "max_1080"
    assert claimed["payload"]["url"] == "http://source.example/master.m3u8"
    with pytest.raises(AppError, match="CONFLICT"):
        service.download(DownloadRequest(inspection_id=job, policy="best"), "click")


def test_expiry_rechecked_in_enqueue_transaction(service):
    job = inspection(service)
    expire(service.repository, job)
    with pytest.raises(AppError, match="INSPECTION_EXPIRED"):
        service.repository.enqueue("download", {}, inspection_id=job)
    assert len(service.repository.recent()) == 1


def test_readonly_output_is_rejected(service, monkeypatch):
    job = inspection(service)

    def denied(*args, **kwargs):
        raise PermissionError("readonly")

    monkeypatch.setattr("idlix_downloader.web.files.tempfile.TemporaryFile", denied)
    with pytest.raises(AppError, match="OUTPUT_UNWRITABLE"):
        service.download(DownloadRequest(inspection_id=job, policy="best"))
    assert len(service.repository.recent()) == 1


def artifact(service):
    repo = service.repository
    job = repo.enqueue("download", {})
    repo.claim("download")
    folder = service.destinations.prepare("default", job)
    path = folder / "media.mp4"
    path.write_bytes(b"fixture")
    repo.finish(job, "completed", result={"artifacts": [{"name": path.name, "size_bytes": 7}]})
    return repo.artifacts_for(job)[0]["id"], path, job


def test_artifacts_reject_symlinks_traversal_and_active_job(service, tmp_path):
    ident, path, job = artifact(service)
    path.unlink()
    target = tmp_path / "outside.mp4"
    target.write_bytes(b"fixture")
    path.symlink_to(target)
    with pytest.raises(AppError, match="ARTIFACT_UNAVAILABLE"):
        service.artifacts.acquire(ident)
    path.unlink()
    path.write_bytes(b"fixture")
    with service.repository.connection() as db:
        db.execute("UPDATE artifacts SET relative_path=? WHERE id=?", ("../../outside.mp4", ident))
    with pytest.raises(AppError, match="ARTIFACT_UNAVAILABLE"):
        service.artifacts.delete(ident)
    with service.repository.connection() as db:
        db.execute("UPDATE jobs SET state='running' WHERE id=?", (job,))
    with pytest.raises(AppError, match="CONFLICT"):
        service.artifacts.delete(ident)
    assert path.exists() and target.exists()


def test_open_local_passes_single_literal_path_without_shell(monkeypatch, tmp_path):
    run = Mock()
    monkeypatch.setattr("idlix_downloader.web.files.sys.platform", "darwin")
    monkeypatch.setattr("idlix_downloader.web.files.subprocess.run", run)
    path = tmp_path / "$(touch nope); film.mp4"
    open_local(path)
    assert run.call_args.args[0] == ["/usr/bin/open", str(path)]
    assert not run.call_args.kwargs.get("shell", False)


def test_probe_format_and_response_byte_limits():
    with pytest.raises(AppError, match="PROBE_LIMIT"):
        LimitedFormats()._limit(([{}] * 13, {}))
    budget = ReadBudget()
    response = Response(io.BytesIO(b"x" * (2 * 1024 * 1024 + 1)), "https://source.example", {}, 200)
    lease = SimpleNamespace(release=Mock())
    bounded = LeasedResponse(response, lease, budget)
    with pytest.raises(RequestError, match="PROBE_LIMIT"):
        bounded.read()
    assert lease.release.called
    budget = ReadBudget()
    budget.remaining = 3
    response = Response(io.BytesIO(b"abcd"), "https://source.example", {}, 200)
    bounded = LeasedResponse(response, SimpleNamespace(release=Mock()), budget)
    with pytest.raises(RequestError, match="PROBE_LIMIT"):
        bounded.read()


def test_direct_probe_reads_only_bounded_sample(monkeypatch):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b"fixture"
    downloader = Mock()
    downloader.urlopen.return_value = response
    monkeypatch.setattr("idlix_downloader.web.inspection.ffmpeg_path", lambda: "/ffmpeg")
    run = Mock(
        return_value=SimpleNamespace(stderr=b"Video: h264, yuv420p, 1920x1080, 24 fps\nAudio: aac")
    )
    monkeypatch.setattr("idlix_downloader.web.inspection.subprocess.run", run)
    info = {"formats": [{"url": "https://source.example/video.mp4", "protocol": "https"}]}
    normalize_dimensions(info, downloader, "https://source.example")
    response.read.assert_called_once_with(SAMPLE_BYTES)
    assert info["formats"][0]["height"] == 1080
    assert info["formats"][0]["acodec"] == "unknown"
    assert "pipe" in run.call_args.args[0] and run.call_args.kwargs["timeout"] == 15
    # Missing dimensions must stay unknown rather than inventing a quality label.
    run.return_value.stderr = b"moov atom not found"
    info = {"formats": [{"url": "https://source.example/video.mp4", "protocol": "https"}]}
    normalize_dimensions(info, downloader, "https://source.example")
    assert choices(info) == []
