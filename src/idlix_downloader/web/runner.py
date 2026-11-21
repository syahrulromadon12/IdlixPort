"""Two independent job slots; subprocess output never becomes a raw user log."""

import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

from .errors import AppError, safe_blocked_host
from .events import PHASES
from .files import Artifacts, Destinations, ensure_space
from .locking import FileLock
from .processes import ProcessTree
from .worker import ERROR_TYPES, MAX_MESSAGE

INSPECTION_TIMEOUT = 120


class Runner:
    def __init__(self, repository, config):
        self.repository, self.config = repository, config
        self.destinations = Destinations(repository.root, config.download_dirs)
        self.artifacts = Artifacts(repository, self.destinations)
        self.lock = FileLock(repository.root / "instance.lock")
        self.stopped = threading.Event()
        self.threads = []
        self.active = {}
        self.mutex = threading.Lock()
        self.failure = False
        self.sessions = None

    @property
    def healthy(self):
        return (
            not self.failure and len(self.threads) == 2 and all(t.is_alive() for t in self.threads)
        )

    def start(self):
        if not self.lock.acquire():
            raise AppError("CONFLICT")
        try:
            # Old POSIX workers detect parent death; never recover while they
            # still hold their slot. Windows Job Objects own process lifetimes.
            for kind in ("inspect", "download"):
                probe = FileLock(self.repository.root / f"worker-{kind}.lock")
                end = time.monotonic() + 3
                while not probe.acquire():
                    if time.monotonic() > end:
                        raise AppError("WORKER_FAILED")
                    time.sleep(0.1)
                probe.release()
            self.repository.recover()
            self.stopped.clear()
            for kind in ("inspect", "download"):
                thread = threading.Thread(target=self._loop, args=(kind,), daemon=True)
                self.threads.append(thread)
                thread.start()
        except BaseException:
            self.stop()
            raise

    def command(self, _job):
        return [sys.executable, "-m", "idlix_downloader.web.worker"]

    def _loop(self, kind):
        try:
            while not self.stopped.is_set():
                job = self.repository.claim(kind)
                if job:
                    self._run(job)
                else:
                    self.stopped.wait(0.1)
        except Exception:
            self.failure = True
            self.stopped.set()

    def _run(self, job):
        tree = None
        result = None
        error = None
        reader = None
        messages = queue.Queue(maxsize=64)
        overflow = threading.Event()
        failed = False
        worker_failure = None
        blocked_host = None
        config, encoded = {}, b""
        try:
            if job["kind"] == "download":
                output = self.destinations.prepare(
                    job["payload"].get("folder_id", "default"),
                    job["id"],
                    job["payload"].get("estimated_bytes"),
                )
            else:
                output = self.repository.root / "work" / job["id"]
            recovery = {}
            if job["payload"].get("video_source"):
                parent = self.repository.get(job["payload"]["video_source"])
                records = self.repository.artifacts_for(parent["id"])
                record = next(
                    (
                        r
                        for r in records
                        if Path(r["relative_path"]).suffix in (".mp4", ".mkv", ".webm")
                    ),
                    None,
                )
                if record is None:
                    raise AppError("ARTIFACT_UNAVAILABLE")
                ensure_space(output, record["size_bytes"])
                lease, _ = self.artifacts.acquire(record["id"])
                name = "media" + Path(record["relative_path"]).suffix
                try:
                    with (output / name).open("xb") as destination:
                        while chunk := lease.handle.read(1024 * 1024):
                            if (
                                self.stopped.is_set()
                                or self.repository.get(job["id"])["cancel_requested"]
                            ):
                                raise AppError("CANCELLED")
                            destination.write(chunk)
                finally:
                    lease.close()
                recovery = {
                    "reuse_video": name,
                    "reuse_quality": parent["result"]["effective_quality"],
                    "source_identity": parent["result"]["source_identity"],
                }
            if self.stopped.is_set() or self.repository.get(job["id"])["cancel_requested"]:
                raise AppError("CANCELLED")
            tree = ProcessTree(self.command(job))
            with self.mutex:
                self.active[job["id"]] = tree
            config = {
                "kind": job["kind"],
                "payload": job["payload"],
                "output": str(output),
                "allowed_hosts": list(self.config.allowed_hosts),
                "network_slots": self.config.network_slots,
                "lock_dir": str(self.repository.root / "locks"),
                "worker_lock": str(self.repository.root / f"worker-{job['kind']}.lock"),
                "parent_pid": os.getpid(),
                **recovery,
            }
            reference = job["payload"].get("session_ref")
            if reference:
                if self.sessions is None:
                    raise AppError("SESSION_EXPIRED")
                config["cookies"] = self.sessions.credentials(reference)
                config["require_account"] = True
            if job["payload"].get("retry_of"):
                previous = self.repository.get(job["payload"]["retry_of"])
                root = self.destinations.root(previous["payload"].get("folder_id", "default"))
                candidate = root / previous["id"]
                if (
                    candidate.is_dir()
                    and not candidate.is_symlink()
                    and candidate.resolve() == candidate
                ):
                    config["resume_from"] = str(candidate)
            encoded = (json.dumps(config, allow_nan=False) + "\n").encode()
            if len(encoded) > MAX_MESSAGE:
                raise AppError("WORKER_FAILED")
            tree.process.stdin.write(encoded)
            tree.process.stdin.close()
            config.pop("cookies", None)
            encoded = b""

            def read():
                try:
                    while line := tree.process.stdout.readline(MAX_MESSAGE + 1):
                        if len(line) > MAX_MESSAGE:
                            overflow.set()
                            return
                        messages.put_nowait(line)
                except (ValueError, OSError, queue.Full):
                    overflow.set()
                finally:
                    try:
                        messages.put_nowait(None)
                    except queue.Full:
                        overflow.set()

            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            started = time.monotonic()
            while True:
                current = self.repository.get(job["id"])
                if self.stopped.is_set() or current["cancel_requested"] or overflow.is_set():
                    raise AppError("WORKER_FAILED")
                if time.monotonic() - started > (
                    INSPECTION_TIMEOUT if job["kind"] == "inspect" else 86400
                ):
                    raise AppError("SOURCE_UNAVAILABLE")
                try:
                    line = messages.get(timeout=0.1)
                except queue.Empty:
                    continue
                if line is None:
                    break

                def invalid_constant(_value):
                    raise ValueError("Non-finite worker data")

                event = json.loads(line, parse_constant=invalid_constant)
                if result is not None or error is not None:
                    raise AppError("WORKER_FAILED")
                if event.get("type") == "progress" and event.get("phase") in PHASES:
                    from .events import Events

                    if event["phase"] == "downloading":
                        Events(lambda safe: self.repository.progress(job["id"], safe)).progress(
                            {"status": "downloading", **event}
                        )
                    else:
                        self.repository.progress(job["id"], {"phase": event["phase"]})
                elif event.get("type") == "result" and isinstance(event.get("result"), dict):
                    result = event["result"]
                    if set(result) - {
                        "title",
                        "choices",
                        "series_url",
                        "episodes",
                        "session_max_height",
                        "effective_quality",
                        "artifacts",
                        "duration",
                        "estimates",
                        "resumed",
                        "subtitles",
                        "subtitle_status",
                        "subtitle_error",
                        "partial",
                        "source_identity",
                    }:
                        raise AppError("WORKER_FAILED")
                elif event.get("type") == "error":
                    error = AppError(event.get("code")).public()
                    if error["code"] == "NETWORK_BLOCKED_DESTINATION":
                        blocked_host = safe_blocked_host(event.get("blocked_host"))
                    exception_type = event.get("exception_type")
                    if isinstance(exception_type, str) and exception_type in ERROR_TYPES:
                        worker_failure = exception_type
                else:
                    raise AppError("WORKER_FAILED")
            exit_code = tree.process.wait(timeout=5)
            if exit_code != 0 or result is None or error:
                raise AppError(error["code"] if error else "WORKER_FAILED")
            if job["kind"] == "download" and not result.get("artifacts"):
                raise AppError("WORKER_FAILED")
            for artifact in result.get("artifacts", []):
                name = artifact["name"]
                root = output
                path = root / name
                if (
                    Path(name).name != name
                    or path.is_symlink()
                    or path.resolve().parent != root.resolve()
                ):
                    raise AppError("WORKER_FAILED")
                if not path.is_file() or path.stat().st_size != artifact["size_bytes"]:
                    raise AppError("WORKER_FAILED")
        except Exception as failure:
            failed = True
            error = AppError(
                failure.code if isinstance(failure, AppError) else "WORKER_FAILED"
            ).public()
            if blocked_host and error["code"] == "NETWORK_BLOCKED_DESTINATION":
                error["blocked_host"] = blocked_host
                error["message"] = f"Server {blocked_host} berada di luar daftar izin IdlixPort."
            if worker_failure is not None:
                error["exception_type"] = worker_failure
            elif error["code"] == "WORKER_FAILED":
                error["exception_type"] = "RunnerError"
        finally:
            config.pop("cookies", None)
            encoded = b""
            if tree:
                tree.stop()
                if reader:
                    reader.join(timeout=2)
                tree.close()
            with self.mutex:
                self.active.pop(job["id"], None)
        if not failed and result and result.get("partial"):
            error = AppError(result.get("subtitle_error", "SUBTITLE_FAILED")).public()
        self.repository.finish(
            job["id"],
            ("interrupted" if self.stopped.is_set() else "failed")
            if failed
            else ("partial" if result.get("partial") else "completed"),
            result=None if failed else result,
            error=error,
        )

        if error and error.get("code") == "SESSION_EXPIRED" and self.sessions:
            self.sessions.expire(job["payload"].get("session_ref"))

    def stop(self):
        self.stopped.set()
        with self.mutex:
            active = list(self.active.values())
        for tree in active:
            tree.stop()
        for thread in self.threads:
            thread.join(timeout=12)
        if any(thread.is_alive() for thread in self.threads):
            self.failure = True
            raise AppError("WORKER_FAILED")
        # A loop can fail after stopping its child but before its terminal commit.
        # Reconcile only after every thread/process and worker lock is released.
        with self.mutex:
            remaining = list(self.active.values())
        for tree in remaining:
            tree.stop()
            tree.close()
        with self.mutex:
            self.active.clear()
        for kind in ("inspect", "download"):
            probe = FileLock(self.repository.root / f"worker-{kind}.lock")
            if not probe.acquire():
                self.failure = True
                raise AppError("WORKER_FAILED")
            probe.release()
        self.repository.recover()
        self.threads.clear()
        self.lock.release()
