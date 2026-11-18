"""Optional real-browser acceptance of P4 using synthetic auth and media."""

import os
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("uvicorn")
import uvicorn
from test_integration import media_server as media_server
from test_web_session_integration import make_session_app


@pytest.fixture
def session_server(tmp_path, media_server, monkeypatch):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    app, profile, _ = make_session_app(
        tmp_path, media_server, monkeypatch, port=port, browser="safari"
    )
    profile.cookie_file.write_text("guest")
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
        yield f"http://127.0.0.1:{port}", profile.cookie_file
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        listener.close()
        assert not thread.is_alive()


@pytest.mark.skipif(
    os.environ.get("IDLIXPORT_BROWSER_TESTS") != "1", reason="Optional Chrome/CDP acceptance"
)
@pytest.mark.parametrize("viewport", ["desktop", "mobile"])
def test_session_browser_flow(session_server, viewport):
    base, cookie = session_server
    result = subprocess.run(
        [
            "node",
            str(Path(__file__).parent / "browser/session-flow.mjs"),
            base,
            viewport,
            str(Path(tempfile.gettempdir()) / "idlixport-browser-artifacts"),
            str(cookie),
        ],
        capture_output=True,
        text=True,
        timeout=100,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"session_flow":"passed"' in result.stdout
