"""Bounded file streaming with a lease held through disconnect and Range requests."""

import re
from pathlib import Path
from urllib.parse import quote

from fastapi.responses import Response, StreamingResponse


def download_filename(record, artifact_id):
    path = Path(record["relative_path"])
    title = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', "-", str(record.get("title") or ""))
    title = title.strip(" .")[:100].encode("utf-8", "ignore")[:160].decode("utf-8", "ignore")
    title = title.rstrip(" .") or f"IdlixPort-{artifact_id[:8]}"
    quality = record.get("quality")
    if type(quality) is int and quality > 0:
        title += f" - {quality}p"
    suffix = path.suffix
    if suffix == ".srt" and re.fullmatch(r"\.[a-z0-9-]{2,16}", Path(path.stem).suffix):
        suffix = Path(path.stem).suffix + suffix
    return title + suffix


def file_response(artifacts, artifact_id, range_header=None):
    lease, record = artifacts.acquire(artifact_id)
    size = record["size_bytes"]
    start, end, status = 0, size - 1, 200
    try:
        if range_header:
            if not range_header.startswith("bytes=") or "," in range_header:
                raise ValueError("Invalid range")
            left, right = range_header[6:].split("-", 1)
            if not left:
                suffix = int(right)
                if suffix <= 0:
                    raise ValueError("Invalid suffix")
                start = max(0, size - suffix)
            else:
                start = int(left)
                end = min(size - 1, int(right)) if right else size - 1
            if start < 0 or start > end or start >= size:
                raise ValueError("Unsatisfiable range")
            status = 206
    except (ValueError, TypeError):
        lease.close()
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Content-Disposition": "attachment; filename*=UTF-8''"
        + quote(download_filename(record, artifact_id), safe=""),
        "X-Content-Type-Options": "nosniff",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    def chunks():
        try:
            lease.handle.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = lease.handle.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        finally:
            lease.close()

    class ArtifactResponse(StreamingResponse):
        async def __call__(self, scope, receive, send):
            try:
                await super().__call__(scope, receive, send)
            finally:
                lease.close()

    return ArtifactResponse(
        chunks(), status_code=status, headers=headers, media_type="application/octet-stream"
    )
