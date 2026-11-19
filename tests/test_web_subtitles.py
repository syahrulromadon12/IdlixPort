"""P5 text-subtitle fetch/remux and partial recovery through real media workers."""

import hashlib
import subprocess
import sys

import pytest
from test_integration import media_server as media_server
from test_web_flow import fixture_worker
from test_web_flow import flow_app as flow_app
from test_web_flow import flow_client as flow_client
from test_web_runner import wait_for

from idlix_downloader.web.runner import Runner

SRT = "1\n00:00:00,000 --> 00:00:00,800\nSynthetic subtitle\n\n"


def subtitle_worker(port):
    helper = fixture_worker(port)
    extra = """
from idlix_downloader.web.inspection import WebGenericIE
original_extract=WebGenericIE._real_extract
def with_subtitles(self,url):
    info=original_extract(self,url)
    if 'missing.m3u8' not in url and 'no-sub' not in url:
        info['subtitles']={'id':[{'url':'http://source.example/id.srt','ext':'srt'}],
                           'en':[{'url':'http://source.example/en.vtt','ext':'vtt'}]}
    return info
WebGenericIE._real_extract=with_subtitles
"""
    return helper.replace("sys.exit(worker.main())", extra + "\nsys.exit(worker.main())")


@pytest.fixture
def subtitle_client(flow_client, media_server, monkeypatch):
    client, app, folder, root, opened = flow_client
    (root / "id.srt").write_text(SRT)
    (root / "en.vtt").write_text("WEBVTT\n\n00:00.000 --> 00:00.800\nSynthetic English\n")
    port = int(media_server[0].rsplit(":", 1)[1])
    helper = subtitle_worker(port)
    monkeypatch.setattr(Runner, "command", lambda self, job: [sys.executable, "-c", helper])
    return client, app, root, media_server[3], media_server[2]


def inspect_source(client, app):
    response = client.post("/api/inspections", json={"url": "http://source.example/master.m3u8"})
    assert response.status_code == 202
    ident = response.json()["id"]
    wait_for(app.state.repository, ident, {"completed", "failed"})
    result = client.get(f"/api/jobs/{ident}").json()
    assert result["state"] == "completed", result
    return result


def job(client, app, inspection, **options):
    response = client.post(
        "/api/jobs", json={"inspection_id": inspection["id"], "policy": "max_1080", **options}
    )
    assert response.status_code == 202, response.text
    ident = response.json()["id"]
    wait_for(app.state.repository, ident, {"completed", "partial", "failed"}, timeout=20)
    return client.get(f"/api/jobs/{ident}").json()


def media_hash(binary, path, stream):
    result = subprocess.run(
        [
            binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            stream,
            "-c",
            "copy",
            "-f",
            "hash",
            "-hash",
            "sha256",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_embed_default_id_and_separate_off_preserve_streams(subtitle_client):
    client, app, root, binary, _ = subtitle_client
    inspection = inspect_source(client, app)
    assert [item["code"] for item in inspection["metadata"]["subtitles"]] == ["en", "id"]
    off = job(client, app, inspection, subtitle_mode="off")
    embedded = job(client, app, inspection)  # Automatic default: Indonesia, MKV soft track.
    assert embedded["state"] == "completed" and embedded["subtitle_status"] == {
        "mode": "embed",
        "language": "id",
    }
    off_path = app.state.artifacts.locate(off["files"][0]["id"])[0]
    path = app.state.artifacts.locate(embedded["files"][0]["id"])[0]
    assert path.suffix == ".mkv"
    for stream in ("0:v:0", "0:a:0"):
        assert media_hash(binary, path, stream) == media_hash(binary, off_path, stream)
    probe = subprocess.run(
        [binary, "-hide_banner", "-i", str(path)], capture_output=True, text=True
    )
    assert (
        "Subtitle: subrip" in probe.stderr
        and "(ind)" in probe.stderr
        and "(default)" in probe.stderr
    )
    extracted = subprocess.run(
        [
            binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            "0:s:0",
            "-f",
            "srt",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Synthetic subtitle" in extracted.stdout
    # Decode the same MKV with its soft subtitle disabled; AV remains playable.
    disabled = subprocess.run(
        [binary, "-hide_banner", "-loglevel", "error", "-i", str(path), "-sn", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert not disabled.stderr
    separate = job(client, app, inspection, subtitle_mode="separate", subtitle_language="en")
    assert len(separate["files"]) == 2 and any(
        f["name"] == "media.en.srt" for f in separate["files"]
    )
    sub = next(f for f in separate["files"] if f["name"].endswith(".srt"))
    assert b"Synthetic English" in client.get(f"/api/artifacts/{sub['id']}/file").content
    assert off["subtitle_status"]["mode"] == "off" and len(off["files"]) == 1


def test_subtitle_failure_retains_video_and_retry_reuses_it(subtitle_client):
    client, app, root, binary, requests = subtitle_client
    inspection = inspect_source(client, app)
    (root / "id.srt").unlink()
    failed = job(client, app, inspection, subtitle_mode="embed", subtitle_language="id")
    assert (
        failed["state"] == "partial" and failed["can_continue"] and failed["files"][0]["available"]
    )
    original = app.state.artifacts.locate(failed["files"][0]["id"])[0].read_bytes()
    (root / "id.srt").write_text(SRT)
    requests.clear()
    response = client.post(
        f"/api/jobs/{failed['id']}/retry", headers={"Idempotency-Key": "subtitle-retry"}
    )
    ident = response.json()["id"]
    wait_for(app.state.repository, ident, {"completed", "partial", "failed"}, timeout=20)
    result = client.get(f"/api/jobs/{ident}").json()
    assert result["state"] == "completed", result
    assert not any(path.endswith(".ts") for path, _, _ in requests)  # Manifests fresh; video local.
    assert app.state.artifacts.locate(failed["files"][0]["id"])[0].read_bytes() == original


def test_continue_without_subtitles_is_explicit_and_does_not_need_network(subtitle_client):
    client, app, root, _, requests = subtitle_client
    inspection = inspect_source(client, app)
    (root / "id.srt").write_text("invalid subtitle")
    failed = job(client, app, inspection, subtitle_mode="embed", subtitle_language="id")
    assert failed["state"] == "partial"
    before = hashlib.sha256(
        app.state.artifacts.locate(failed["files"][0]["id"])[0].read_bytes()
    ).hexdigest()
    requests.clear()
    response = client.post(f"/api/jobs/{failed['id']}/continue-without-subtitles")
    assert response.status_code == 200 and response.json()["state"] == "completed"
    assert response.json()["subtitle_status"]["accepted_without_subtitles"]
    assert client.post(f"/api/jobs/{failed['id']}/continue-without-subtitles").status_code == 200
    assert requests == []
    assert (
        hashlib.sha256(
            app.state.artifacts.locate(failed["files"][0]["id"])[0].read_bytes()
        ).hexdigest()
        == before
    )


def test_wrong_language_and_paths_are_rejected_before_enqueue(subtitle_client):
    client, app, *_ = subtitle_client
    inspection = inspect_source(client, app)
    body = {
        "inspection_id": inspection["id"],
        "policy": "best",
        "subtitle_mode": "embed",
        "subtitle_language": "fr",
    }
    assert client.post("/api/jobs", json=body).status_code == 422
    assert (
        client.post("/api/jobs", json={**body, "subtitle_language": "../../secret"}).status_code
        == 422
    )
