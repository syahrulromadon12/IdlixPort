"""Registered local destinations and artifact lifetimes, never browser-supplied paths."""

import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from collections import Counter
from pathlib import Path

from .errors import AppError

RESERVE = 1024**3


def ensure_space(path, estimated_bytes=None):
    required = RESERVE + (int(estimated_bytes * 2.5) if estimated_bytes else 0)
    try:
        if shutil.disk_usage(path).free < required:
            raise AppError("DISK_FULL")
    except OSError as error:
        raise AppError("OUTPUT_UNWRITABLE") from error


def open_local(path):
    """Only call with a path resolved from a registered artifact/destination."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))
        else:
            command = "/usr/bin/open" if sys.platform == "darwin" else shutil.which("xdg-open")
            if not command:
                raise AppError("OPEN_UNAVAILABLE")
            subprocess.run(
                [command, str(path)],
                check=True,
                timeout=5,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except (OSError, subprocess.SubprocessError) as error:
        raise AppError("OPEN_UNAVAILABLE") from error


class Destinations:
    def __init__(self, data_dir, extra=()):
        default = (Path(data_dir) / "downloads").resolve()
        self.roots = {"default": default}
        for value in extra:
            root = Path(value).expanduser().resolve()
            if root not in self.roots.values():
                key = hashlib.sha256(os.path.normcase(str(root)).encode()).hexdigest()[:16]
                self.roots[key] = root

    def root(self, folder_id):
        root = self.roots.get(folder_id)
        if root is None or root.is_symlink() or root.resolve() != root:
            raise AppError("FOLDER_UNAVAILABLE")
        return root

    def listing(self):
        return [
            {"id": key, "label": "Unduhan IdlixPort" if key == "default" else root.name}
            for key, root in self.roots.items()
        ]

    def check(self, folder_id, estimated_bytes=None):
        root = self.root(folder_id)
        try:
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=root) as handle:
                handle.write(b"storage-check")
                handle.flush()
            ensure_space(root, estimated_bytes)
        except OSError as error:
            raise AppError("OUTPUT_UNWRITABLE") from error
        return root

    def prepare(self, folder_id, job_id, estimated_bytes=None):
        root = self.check(folder_id, estimated_bytes)
        path = root / job_id
        if path.is_symlink() or path.resolve().parent != root:
            raise AppError("FOLDER_UNAVAILABLE")
        try:
            path.mkdir(mode=0o700, exist_ok=True)
        except OSError as error:
            raise AppError("OUTPUT_UNWRITABLE") from error
        return path


class ArtifactLease:
    def __init__(self, handle, release):
        self.handle, self.release = handle, release
        self.closed = False

    def close(self):
        if not self.closed:
            self.closed = True
            self.handle.close()
            self.release()


class Artifacts:
    def __init__(self, repository, destinations):
        self.repository, self.destinations = repository, destinations
        self.lock = threading.RLock()
        self.readers = Counter()

    def locate(self, artifact_id):
        record = self.repository.artifact(artifact_id)
        job = self.repository.get(record["job_id"])
        if job["state"] not in ("completed", "partial"):
            raise AppError("CONFLICT")
        root = self.destinations.root(job["payload"].get("folder_id", "default"))
        parts = Path(record["relative_path"]).parts
        if len(parts) != 3 or parts[:2] != ("downloads", job["id"]):
            raise AppError("ARTIFACT_UNAVAILABLE")
        path = root / job["id"] / parts[2]
        if (
            path.parent.is_symlink()
            or path.is_symlink()
            or path.resolve().parent != root / job["id"]
            or not path.is_file()
            or path.stat().st_size != record["size_bytes"]
        ):
            raise AppError("ARTIFACT_UNAVAILABLE")
        return path, record, job

    def acquire(self, artifact_id):
        with self.lock:
            path, record, _ = self.locate(artifact_id)
            handle = None
            try:
                descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                handle = os.fdopen(descriptor, "rb")
                actual = os.fstat(handle.fileno())
                expected = path.stat()
                if (
                    not stat.S_ISREG(actual.st_mode)
                    or path.is_symlink()
                    or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)
                ):
                    handle.close()
                    raise AppError("ARTIFACT_UNAVAILABLE")
            except OSError as error:
                if handle is not None:
                    handle.close()
                raise AppError("ARTIFACT_UNAVAILABLE") from error
            self.readers[artifact_id] += 1

            def release():
                with self.lock:
                    self.readers[artifact_id] -= 1
                    if not self.readers[artifact_id]:
                        del self.readers[artifact_id]

            return ArtifactLease(handle, release), record

    def delete(self, artifact_id):
        with self.lock:
            if self.readers[artifact_id]:
                raise AppError("CONFLICT")
            path, record, _ = self.locate(artifact_id)
            for ident in self.repository.active_ids():
                if self.repository.get(ident)["payload"].get("video_source") == record["job_id"]:
                    raise AppError("CONFLICT")
            try:
                path.unlink()
            except OSError as error:
                raise AppError("ARTIFACT_UNAVAILABLE") from error
            self.repository.delete_artifact(artifact_id)

    def open(self, artifact_id):
        with self.lock:
            path, _, _ = self.locate(artifact_id)
            open_local(path)

    def open_folder(self, job_id):
        job = self.repository.get(job_id)
        if job["kind"] != "download" or job["state"] not in ("completed", "partial"):
            raise AppError("CONFLICT")
        root = self.destinations.root(job["payload"].get("folder_id", "default"))
        path = root / job_id
        if path.is_symlink() or not path.is_dir() or path.resolve().parent != root:
            raise AppError("ARTIFACT_UNAVAILABLE")
        open_local(path)

    def output_folder(self, job):
        root = self.destinations.root(job["payload"].get("folder_id", "default"))
        path = root / job["id"]
        if path.is_symlink() or path.resolve() != path:
            raise AppError("ARTIFACT_UNAVAILABLE")
        return path

    def storage_summary(self, job):
        try:
            path = self.output_folder(job)
            entries = list(path.iterdir()) if path.is_dir() else []
            regular = [p for p in entries if not p.is_symlink() and p.is_file()]
            return {
                "bytes": sum(p.stat().st_size for p in regular),
                "partial": job["state"] != "completed" and bool(regular),
                "files_deleted": bool((job["result"] or {}).get("files_deleted")),
            }
        except (AppError, OSError):
            return {
                "bytes": 0,
                "partial": False,
                "files_deleted": bool((job["result"] or {}).get("files_deleted")),
            }

    def delete_results(self, job_id):
        """Retain history; delete only this terminal attempt's local outputs."""
        with self.lock:
            job = self.repository.get(job_id)
            if job["kind"] != "download" or job["state"] not in {
                "completed",
                "partial",
                "failed",
                "cancelled",
                "interrupted",
            }:
                raise AppError("CONFLICT")
            if any(self.readers[row["id"]] for row in self.repository.artifacts_for(job_id)):
                raise AppError("CONFLICT")
            for ident in self.repository.active_ids():
                if job_id in (
                    self.repository.get(ident)["payload"].get("retry_of"),
                    self.repository.get(ident)["payload"].get("video_source"),
                ):
                    raise AppError("CONFLICT")
            path = self.output_folder(job)
            try:
                entries = list(path.iterdir()) if path.exists() else []
                if any(p.is_symlink() or not p.is_file() for p in entries):
                    raise AppError("ARTIFACT_UNAVAILABLE")
                for entry in entries:
                    entry.unlink()
                if path.exists():
                    path.rmdir()
            except OSError as error:
                raise AppError("OUTPUT_UNWRITABLE") from error
            self.repository.clear_artifacts(job_id)
