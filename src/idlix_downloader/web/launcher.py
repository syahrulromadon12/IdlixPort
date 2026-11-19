"""Open the local UI only after the IdlixPort worker is ready."""

import json
import os
import socket
import sys
import threading
import time
import webbrowser
from urllib.request import ProxyHandler, build_opener

import uvicorn

from ..downloader import ffmpeg_path
from .app import create_app


def launch(config):
    url = f"http://127.0.0.1:{config.port}/"
    opener = build_opener(ProxyHandler({}))

    def ready():
        try:
            with opener.open(url + "health/ready", timeout=0.5) as response:
                return json.loads(response.read(1024)) == {
                    "status": "ready",
                    "application": "idlixport",
                }
        except (OSError, ValueError):
            return False

    def open_ui():
        print(f"IdlixPort siap di {url}", flush=True)
        if not webbrowser.open(url):
            print(f"Buka {url} di browser.", file=sys.stderr)

    with socket.socket() as listener:
        if os.name != "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", config.port))
        except OSError:
            if ready():
                open_ui()
                return 0
            print(f"Port {config.port} sedang dipakai. Coba --port lain.", file=sys.stderr)
            return 1
        if not ffmpeg_path():
            print("FFmpeg tidak ditemukan. Jalankan idlixport doctor.", file=sys.stderr)
            return 1
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(config), proxy_headers=False, access_log=False, log_level="warning"
            )
        )
        done = threading.Event()

        def wait_for_ui():
            deadline = time.monotonic() + 20
            while not done.wait(0.15):
                if ready():
                    open_ui()
                    return
                if time.monotonic() >= deadline:
                    print("Backend belum siap. Periksa pesan startup.", file=sys.stderr)
                    server.should_exit = True
                    return

        waiter = threading.Thread(target=wait_for_ui, daemon=True)
        waiter.start()
        try:
            server.run(sockets=[listener])
        finally:
            done.set()
            waiter.join(timeout=2)
        if not server.started:
            print("IdlixPort gagal mulai. Periksa folder data dan instance aktif.", file=sys.stderr)
        return 0 if server.started else 1
