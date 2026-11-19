"""Opt-in, process-local browser sessions with generation-checked worker access."""

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from urllib.parse import urlsplit

from ..extractor import IdlixIE
from .browser_profiles import discover_profiles
from .errors import AppError
from .network import NetworkPolicy
from .processes import ProcessTree
from .session_cookies import usable
from .worker import MAX_MESSAGE

CONNECT_TIMEOUT = 120
MIN_INTERVAL = 5


class Sessions:
    def __init__(self, repository, config, runner):
        self.repository, self.config, self.runner = repository, config, runner
        self.profiles = discover_profiles()
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.thread = None
        self.tree = None
        self.state = "disconnected"
        self.reference = None
        self.revision = 0
        self.cookies = []
        self.fingerprint = None
        self.selected = None
        self.url = None
        self.code = None
        self.account_verified = False
        self.media_ready = False
        self.max_height = None
        self.deadline = self.next_probe = 0
        self.failures = 0
        self.manual_login = False
        self.preference = repository.root / "session-preferences.json"
        try:
            if self.preference.is_symlink() or self.preference.stat().st_size > 1024:
                raise ValueError("Invalid preferences")
            self.selected = json.loads(self.preference.read_text()).get("profile_id")
        except (OSError, ValueError, AttributeError):
            pass

    def start(self):
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def status(self):
        with self.lock:
            return {
                "state": self.state,
                "profile_id": self.selected,
                "revision": self.revision,
                "account_verified": self.account_verified,
                "media_ready": self.media_ready,
                "max_height": self.max_height,
                "manual_login": self.manual_login,
                "login_url": self.login_url if self.url else None,
                "error": AppError(self.code).public() if self.code else None,
                "profiles": [profile.public() for profile in self.profiles.values()],
            }

    @property
    def login_url(self):
        parsed = urlsplit(self.url)
        return f"{parsed.scheme}://{parsed.netloc}/"

    def connect(self, profile_id, url, consent):
        if not consent:
            raise AppError("CONSENT_REQUIRED")
        try:
            NetworkPolicy(self.config.allowed_hosts).validate(url)
        except (OSError, ValueError) as error:
            raise AppError("NETWORK_BLOCKED") from error
        if not IdlixIE.suitable(url) or urlsplit(url).scheme != "https":
            raise AppError("INVALID_INPUT")
        profile = self.profiles.get(profile_id)
        if profile is None:
            raise AppError("BROWSER_UNAVAILABLE")
        with self.lock:
            if self.state != "disconnected":
                raise AppError("CONFLICT")
            self.selected, self.url = profile_id, url
            self.manual_login = not profile.login(self.login_url)
            # Only an opaque catalog ID is persisted. Consent and cookies are not.
            temporary = self.preference.with_suffix(".tmp")
            if temporary.is_symlink() or self.preference.is_symlink():
                raise AppError("STORAGE_UNAVAILABLE")
            temporary.write_text(json.dumps({"profile_id": profile_id}))
            temporary.chmod(0o600)
            temporary.replace(self.preference)
            self.reference = uuid.uuid4().hex
            self.state, self.code = "connecting", None
            self.deadline = time.monotonic() + CONNECT_TIMEOUT
            self.next_probe = 0
            self.wake.set()
        return self.status()

    def retry(self):
        with self.lock:
            if self.state not in ("error", "expired") or not self.reference:
                raise AppError("CONFLICT")
            self.reference = uuid.uuid4().hex
            self.state, self.code = "connecting", None
            self.deadline = time.monotonic() + CONNECT_TIMEOUT
            self.next_probe, self.failures = 0, 0
            self.wake.set()
        return self.status()

    def focus(self):
        # A focus event can wake the scheduler, never bypass debounce or deadline.
        with self.lock:
            if self.state == "connecting":
                self.wake.set()
        return self.status()

    @contextmanager
    def binding(self, url, *, expected="current", revision=None):
        with self.lock:
            if revision is not None and revision != self.revision:
                raise AppError("INSPECTION_EXPIRED")
            reference = self.reference if self.state == "connected" else None
            if expected != "current":
                if expected is not None and (expected != reference or self.state != "connected"):
                    raise AppError("SESSION_EXPIRED")
                reference = expected
            if reference and urlsplit(url).hostname != urlsplit(self.url).hostname:
                raise AppError("SESSION_EXPIRED")
            yield {"session_ref": reference, "session_revision": self.revision}

    def accepts(self, reference):
        with self.lock:
            return self.state == "connected" and self.reference == reference

    def credentials(self, reference):
        with self.lock:
            if self.state != "connected" or reference != self.reference:
                raise AppError("SESSION_EXPIRED")
            values = usable(self.cookies)
            if self.cookies and not values:
                raise AppError("SESSION_EXPIRED")
            return [dict(value) for value in values]

    def command(self):
        return [sys.executable, "-m", "idlix_downloader.web.browser_worker"]

    def _probe(self, reference):
        with self.lock:
            if reference != self.reference or self.state != "connecting":
                return None
            profile = self.profiles[self.selected]
            fingerprint = profile.fingerprint(urlsplit(self.url).hostname)
            reload_cookies = self.fingerprint != fingerprint
            config = {
                "kind": "inspect",
                "payload": {"url": self.url},
                "output": str(self.repository.root / "session-probe"),
                "allowed_hosts": list(self.config.allowed_hosts),
                "network_slots": self.config.network_slots,
                "lock_dir": str(self.repository.root / "locks"),
                "parent_pid": os.getpid(),
                "cookies": self.cookies,
            }
            if reload_cookies:
                config.update(
                    browser=profile.browser,
                    profile=str(
                        profile.cookie_file if profile.browser == "safari" else profile.path
                    ),
                )
            tree = ProcessTree(self.command())
            self.tree = tree
            timeout = max(0.1, self.deadline - time.monotonic())
        try:
            encoded = (json.dumps(config, allow_nan=False) + "\n").encode()
            if len(encoded) > MAX_MESSAGE:
                raise AppError("COOKIE_UNAVAILABLE")
            output, _ = tree.process.communicate(encoded, timeout=timeout)
            if len(output) > MAX_MESSAGE or tree.process.returncode != 0:
                raise AppError("COOKIE_UNAVAILABLE")
            result = json.loads(output)
            if not isinstance(result, dict) or not isinstance(result.get("cookies", []), list):
                raise AppError("COOKIE_UNAVAILABLE")
            with self.lock:
                if reference != self.reference or self.state != "connecting":
                    return None
                if "cookies" in result:
                    self.cookies = result.pop("cookies")
                    self.fingerprint = fingerprint
            return result
        except subprocess.TimeoutExpired:
            return {"code": "SESSION_TIMEOUT"}
        except Exception:
            return {"code": "COOKIE_UNAVAILABLE"}
        finally:
            tree.stop()
            tree.close()
            with self.lock:
                if self.tree is tree:
                    self.tree = None

    def _loop(self):
        while not self.stopped.is_set():
            self.wake.wait(0.25)
            self.wake.clear()
            with self.lock:
                if self.state != "connecting":
                    continue
                now = time.monotonic()
                if now >= self.deadline:
                    self.state, self.code = "error", self.code or "SESSION_TIMEOUT"
                    continue
                if now < self.next_probe:
                    continue
                reference = self.reference
                self.next_probe = now + MIN_INTERVAL
            try:
                result = self._probe(reference)
            except Exception:
                result = {"code": "COOKIE_UNAVAILABLE"}
            with self.lock:
                if not result or reference != self.reference or self.state != "connecting":
                    continue
                self.account_verified = bool(result.get("account_verified"))
                self.media_ready = bool(result.get("media_ready"))
                self.max_height = result.get("max_height")
                self.code = result.get("code")
                if self.account_verified and self.media_ready:
                    self.repository.invalidate_inspections()
                    self.revision += 1
                    self.state, self.code = "connected", None
                elif self.code in (
                    "COOKIE_PERMISSION",
                    "COOKIE_UNAVAILABLE",
                    "VERIFICATION_REQUIRED",
                    "SESSION_TIMEOUT",
                    "SESSION_EXPIRED",
                ):
                    self.state = "error"
                else:
                    self.failures += 1
                    self.next_probe = time.monotonic() + min(30, MIN_INTERVAL * self.failures)

    def disconnect(self, *, expired=False):
        with self.lock:
            if self.state == "disconnected":
                return self.status()
            reference, tree = self.reference, self.tree
            self.state = "disconnecting"
            self.revision += 1
            self.repository.invalidate_inspections()
            related = []
            for ident in self.repository.active_ids():
                if self.repository.get(ident)["payload"].get("session_ref") == reference:
                    self.repository.cancel(ident)
                    related.append(ident)
        if tree:
            tree.stop()
        deadline = time.monotonic() + 12
        while tree and self.tree is tree:
            if time.monotonic() >= deadline:
                raise AppError("WORKER_FAILED")
            time.sleep(0.05)
        while any(
            self.repository.get(ident)["state"] in ("queued", "running", "cancelling")
            for ident in related
        ):
            if time.monotonic() >= deadline:
                raise AppError("WORKER_FAILED")
            time.sleep(0.05)
        with self.lock:
            self.cookies.clear()
            self.cookies, self.fingerprint = [], None
            self.account_verified = self.media_ready = False
            self.max_height = None
            self.state = "expired" if expired else "disconnected"
            self.code = "SESSION_EXPIRED" if expired else None
            if not expired:
                self.reference = self.url = None
        return self.status()

    def expire(self, reference):
        with self.lock:
            if reference != self.reference or self.state != "connected":
                return
        self.disconnect(expired=True)

    def stop(self):
        self.disconnect()
        self.stopped.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=12)
            if self.thread.is_alive():
                raise AppError("WORKER_FAILED")
