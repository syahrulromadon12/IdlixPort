"""Use RAM snapshots for yt-dlp cookie extraction; never copy browser DBs to disk."""

import sqlite3
import time
from contextlib import contextmanager
from urllib.parse import quote

from yt_dlp import cookies as browser_cookies

from .errors import AppError


def domains_for(host):
    labels = host.split(".")
    return [
        host,
        "." + host,
        *("." + ".".join(labels[index:]) for index in range(1, len(labels) - 1)),
    ]


def memory_database(path, browser, host):
    memory = sqlite3.connect(":memory:")
    deadline = time.monotonic() + 10

    def progress(*_args):
        if time.monotonic() > deadline:
            raise AppError("COOKIE_UNAVAILABLE")

    try:
        memory.execute("PRAGMA temp_store=MEMORY")
        uri = "file:" + quote(str(path), safe="/:") + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=0.5) as source:
            source.backup(memory, pages=256, progress=progress, sleep=0.01)
        table, column = ("moz_cookies", "host") if browser == "firefox" else ("cookies", "host_key")
        domains = domains_for(host)
        marks = ",".join("?" for _ in domains)
        memory.execute(f"DELETE FROM {table} WHERE {column} NOT IN ({marks})", domains)
        memory.commit()
        return memory.cursor()
    except BaseException:
        memory.close()
        raise


@contextmanager
def memory_cookie_reader(browser, host):
    # This patch exists only in the isolated probe process. yt-dlp may create
    # an empty temporary directory, but no cookie database is written into it.
    original = browser_cookies._open_database_copy
    browser_cookies._open_database_copy = lambda path, _directory: memory_database(
        path, browser, host
    )
    try:
        yield
    finally:
        browser_cookies._open_database_copy = original
