"""Server-owned browser/profile catalog. The API never accepts a filesystem path."""

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .browser_storage import domains_for
from .errors import AppError


@dataclass(frozen=True)
class Profile:
    browser: str
    path: Path
    cookie_file: Path
    executable: str | None = None
    application: str | None = None

    @property
    def id(self):
        return hashlib.sha256((self.browser + str(self.path)).encode()).hexdigest()[:24]

    def public(self):
        return {
            "id": self.id,
            "label": "Safari · Standar"
            if self.browser == "safari"
            else f"{self.browser.title()} · {self.path.name}",
            "automatic_login": self.browser in ("chrome", "edge", "brave"),
        }

    def fingerprint(self, host=None):
        # Chromium's WAL changes for every site. Hash only the authorized source
        # rows without decrypting, so unrelated browsing cannot retrigger Keychain.
        if host and self.browser in ("chrome", "edge", "brave"):
            domains = domains_for(host)
            try:
                uri = "file:" + quote(str(self.cookie_file), safe="/:") + "?mode=ro"
                with sqlite3.connect(uri, uri=True, timeout=0.2) as db:
                    marks = ",".join("?" for _ in domains)
                    rows = db.execute(
                        "SELECT host_key,name,path,expires_utc,is_secure,encrypted_value,value "
                        f"FROM cookies WHERE host_key IN ({marks}) ORDER BY host_key,name,path",
                        domains,
                    ).fetchall()
                digest = hashlib.sha256(repr(rows).encode()).hexdigest()
                return ("source-cookies", digest)
            except (OSError, sqlite3.Error):
                pass
        result = []
        for path in (self.cookie_file, Path(str(self.cookie_file) + "-wal")):
            try:
                value = path.stat()
                result.append((value.st_mtime_ns, value.st_size))
            except OSError:
                result.append(None)
        return tuple(result)

    def login(self, url):
        if self.browser not in ("chrome", "edge", "brave"):
            return False
        if self.cookie_file.is_symlink() or self.path.is_symlink():
            raise AppError("BROWSER_UNAVAILABLE")
        args = ["--profile-directory=" + self.path.name, url]
        if self.application and sys.platform == "darwin":
            command = ["/usr/bin/open", "-a", self.application, "--args", *args]
        elif self.executable:
            command = [self.executable, "--user-data-dir=" + str(self.path.parent), *args]
        else:
            return False
        try:
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=os.name != "nt",
            )
        except OSError as error:
            raise AppError("BROWSER_UNAVAILABLE") from error
        return True


def discover_profiles():
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library/Application Support"
        roots = [
            ("chrome", base / "Google/Chrome", "Google Chrome"),
            ("edge", base / "Microsoft Edge", "Microsoft Edge"),
            ("brave", base / "BraveSoftware/Brave-Browser", "Brave Browser"),
        ]
        firefox = base / "Firefox/Profiles"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
        roots = [
            ("chrome", base / "Google/Chrome/User Data", None),
            ("edge", base / "Microsoft/Edge/User Data", None),
            ("brave", base / "BraveSoftware/Brave-Browser/User Data", None),
        ]
        firefox = (
            Path(os.environ.get("APPDATA", home / "AppData/Roaming")) / "Mozilla/Firefox/Profiles"
        )
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
        roots = [
            ("chrome", base / "google-chrome", None),
            ("edge", base / "microsoft-edge", None),
            ("brave", base / "BraveSoftware/Brave-Browser", None),
        ]
        firefox = home / ".mozilla/firefox"
    result = []
    for browser, root, application in roots:
        executable = shutil.which(
            {"chrome": "google-chrome", "edge": "microsoft-edge", "brave": "brave-browser"}[browser]
        )
        if sys.platform == "win32":
            candidate = (
                root.parent
                / "Application"
                / (browser + ".exe" if browser != "edge" else "msedge.exe")
            )
            executable = str(candidate) if candidate.is_file() else None
        try:
            for folder in root.iterdir():
                if folder.name != "Default" and not folder.name.startswith("Profile "):
                    continue
                for relative in ("Network/Cookies", "Cookies"):
                    cookie = folder / relative
                    if cookie.is_file() and not cookie.is_symlink() and folder.resolve() == folder:
                        result.append(Profile(browser, folder, cookie, executable, application))
                        break
        except OSError:
            continue
    try:
        for folder in firefox.iterdir():
            cookie = folder / "cookies.sqlite"
            if cookie.is_file() and not cookie.is_symlink() and folder.resolve() == folder:
                result.append(Profile("firefox", folder, cookie))
    except OSError:
        pass
    if sys.platform == "darwin":
        for folder in (
            home / "Library/Cookies",
            home / "Library/Containers/com.apple.Safari/Data/Library/Cookies",
        ):
            cookie = folder / "Cookies.binarycookies"
            try:
                if cookie.is_file() and not cookie.is_symlink():
                    result.append(Profile("safari", folder, cookie))
                    break
            except OSError:
                continue
    return {profile.id: profile for profile in result[:32]}
