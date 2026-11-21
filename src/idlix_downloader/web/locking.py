"""OS locks survive worker crashes without stale PID heuristics."""

import os
import time
from pathlib import Path


class FileLock:
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                if self.path.stat().st_size == 0:
                    file.write(b"0")
                    file.flush()
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            file.close()
            return False
        self.file = file
        return True

    def release(self):
        if self.file:
            if os.name == "nt":
                import msvcrt

                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.file.close()
            self.file = None


class SlotPool:
    def __init__(self, directory, count):
        self.directory, self.count = Path(directory), count

    def acquire(self, timeout=20):
        end = time.monotonic() + timeout
        while True:
            for i in range(self.count):
                lock = FileLock(self.directory / f"network-{i}.lock")
                if lock.acquire():
                    return lock
            if time.monotonic() >= end:
                raise TimeoutError("network_capacity_timeout")
            time.sleep(0.05)
