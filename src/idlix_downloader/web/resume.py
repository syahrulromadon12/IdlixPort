"""Conservative HTTP resume; segmented streams always start in a fresh directory."""

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from yt_dlp.networking import Request


def prepare_resume(downloader, formats, effective, output, previous=None, *, in_place=False):
    """Require strong ETag, byte ranges, identical stream layout and sampled prefix."""
    if (
        len(formats) != 1
        or formats[0].get("protocol") not in ("http", "https")
        or formats[0].get("ext") != "mp4"
    ):
        return False
    item = formats[0]
    url = urlsplit(item["url"])
    identity = hashlib.sha256((url.scheme + "://" + url.netloc + url.path).encode()).hexdigest()
    headers = {
        **downloader.params.get("http_headers", {}),
        **item.get("http_headers", {}),
        "Range": "bytes=0-65535",
    }
    try:
        with downloader.urlopen(Request(item["url"], headers=headers)) as response:
            etag = response.headers.get("ETag", "")
            content = re.fullmatch(
                r"bytes 0-(\d+)/(\d+)", response.headers.get("Content-Range", "")
            )
            if response.status != 206 or not content or not re.fullmatch(r'"[^"\r\n]+"', etag):
                return False
            prefix = response.read(65536)
            if len(prefix) != min(65536, int(content[2])):
                return False
        fingerprint = {
            "identity": identity,
            "quality": effective,
            "etag": hashlib.sha256(etag.encode()).hexdigest(),
            "size": int(content[2]),
            "ext": item["ext"],
        }
        resumed = False
        if previous or in_place:
            previous = output if in_place else Path(previous)
            sidecar = previous / ".resume.json"
            source = previous / ("media." + item["ext"] + ".part")
            if (
                not previous.is_symlink()
                and previous.resolve() == previous
                and not sidecar.is_symlink()
                and sidecar.is_file()
                and sidecar.stat().st_size <= 8192
                and not source.is_symlink()
                and source.is_file()
                and 0 < source.stat().st_size < fingerprint["size"]
                and json.loads(sidecar.read_text()) == fingerprint
            ):
                with source.open("rb") as handle:
                    sample = handle.read(min(65536, source.stat().st_size))
                if sample == prefix[: len(sample)]:
                    if in_place:
                        item["http_headers"] = {**item.get("http_headers", {}), "If-Range": etag}
                        return True
                    destination_path = output / source.name
                    # A failed copy must not become an implicit resume candidate.
                    try:
                        with (
                            source.open("rb") as handle,
                            destination_path.open("xb") as destination,
                        ):
                            shutil.copyfileobj(handle, destination, 1024 * 1024)
                    except BaseException:
                        destination_path.unlink(missing_ok=True)
                        raise
                    resumed = True
        if in_place:
            return False
        # If the representation changes between probe and transfer, If-Range forces
        # a full response. yt-dlp restarts instead of appending an incompatible body.
        item["http_headers"] = {**item.get("http_headers", {}), "If-Range": etag}
        descriptor = os.open(output / ".resume.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(fingerprint, handle, allow_nan=False)
        return resumed
    except (OSError, ValueError):
        # Invalid or vanished old partials are not a reason to use them.
        return False
