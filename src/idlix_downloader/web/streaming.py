"""Bounded file streaming with a lease held through disconnect and Range requests."""

from urllib.parse import quote

from fastapi.responses import Response, StreamingResponse


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
        + quote("IdlixPort-" + artifact_id[:8] + "." + record["relative_path"].rsplit(".", 1)[-1]),
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
