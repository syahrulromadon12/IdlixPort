"""Optional P5 real-browser flow with synthetic caption and movie sources."""

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("uvicorn")
import uvicorn
from test_integration import media_server as media_server
from test_web_subtitles import SRT, subtitle_worker

from idlix_downloader.web.app import create_app
from idlix_downloader.web.config import WebConfig
from idlix_downloader.web.runner import Runner


@pytest.fixture
def p5_server(tmp_path, media_server, monkeypatch):
    root = media_server[1]
    (root / "id.srt").write_text(SRT)
    (root / "en.vtt").write_text("WEBVTT\n\n00:00.000 --> 00:00.800\nSynthetic English\n")
    helper = subtitle_worker(int(media_server[0].rsplit(":", 1)[1]))
    monkeypatch.setattr(Runner, "command", lambda self, job: [sys.executable, "-c", helper])
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    app = create_app(WebConfig(tmp_path / "data", port=port, allowed_hosts=("source.example",)))
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    try:
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline
            time.sleep(0.05)
        yield f"http://127.0.0.1:{port}", root / "id.srt"
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        listener.close()
        assert not thread.is_alive()


@pytest.mark.skipif(
    os.environ.get("IDLIXPORT_BROWSER_TESTS") != "1", reason="Optional Chrome/CDP acceptance"
)
@pytest.mark.parametrize("viewport", ["desktop", "mobile"])
def test_subtitle_batch_browser(p5_server, viewport):
    base, caption = p5_server
    result = subprocess.run(
        [
            "node",
            str(Path(__file__).parent / "browser/subtitle-batch-flow.mjs"),
            base,
            viewport,
            str(Path(tempfile.gettempdir()) / "idlixport-browser-artifacts"),
            str(caption),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"p5_flow":"passed"' in result.stdout
