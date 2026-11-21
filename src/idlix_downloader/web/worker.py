"""Internal single-job entry point. Input and output are bounded private JSON pipes."""

import json
import os
import signal
import sys
import threading
import time

from .engine import run_job
from .errors import public_error
from .events import Events
from .files import ensure_space
from .locking import FileLock

MAX_MESSAGE = 65536
ERROR_TYPES = frozenset({
    "AssertionError", "DownloadError", "FileNotFoundError", "HTTPError",
    "IncompleteRead", "IndexError", "KeyError", "OSError", "RequestError",
    "RuntimeError", "TimeoutError", "TypeError", "ValueError",
})


def emit(data):
    value = json.dumps(data, allow_nan=False)
    if len(value.encode()) > MAX_MESSAGE:
        raise ValueError("Worker event exceeds limit")
    print(value, flush=True)


def monitor_parent(parent):
    while True:
        if os.getppid() != parent:
            os.killpg(os.getpgrp(), signal.SIGKILL)
        time.sleep(0.25)


def main():
    os.umask(0o077)
    lock = None
    try:
        line = sys.stdin.buffer.readline(MAX_MESSAGE + 1)
        if len(line) > MAX_MESSAGE:
            raise ValueError("Worker input exceeds limit")
        config = json.loads(line)
        lock = FileLock(config["worker_lock"])
        if not lock.acquire():
            raise ValueError("Worker slot already occupied")
        if os.name != "nt":
            threading.Thread(
                target=monitor_parent, args=(config["parent_pid"],), daemon=True
            ).start()

        def checked_emit(event):
            if config["kind"] == "download" and event.get("phase") in (
                "downloading",
                "merging",
                "validating",
                "subtitles",
            ):
                ensure_space(config["output"])
            emit(event)

        result = run_job(config, Events(checked_emit))
        emit({"type": "result", "result": result})
        return 0
    except Exception as error:
        failure = public_error(error)
        error_type = type(error).__name__
        emit({
            "type": "error",
            "code": failure["code"],
            "blocked_host": failure.get("blocked_host"),
            "exception_type": error_type if error_type in ERROR_TYPES else "UnknownError",
        })
        return 1
    finally:
        if lock:
            lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
