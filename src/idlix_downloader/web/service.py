"""Public projections and server-owned download specs; no raw media metadata escapes."""

import hashlib
import json
import re
import time
from contextlib import nullcontext

from .engine import entrypoint
from .errors import AppError
from .network import NetworkPolicy


def request_hash(kind, data):
    return hashlib.sha256((kind + json.dumps(data, sort_keys=True)).encode()).hexdigest()


def idempotency_key(value):
    if value is not None and not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", value):
        raise AppError("INVALID_INPUT")
    return value


class Service:
    def __init__(self, repository, config, destinations, artifacts, sessions=None):
        self.repository, self.config = repository, config
        self.destinations, self.artifacts = destinations, artifacts
        self.sessions = sessions

    def inspect(self, url, key=None):
        try:
            entrypoint(url)
            NetworkPolicy(self.config.allowed_hosts).validate(url)
        except (OSError, ValueError) as error:
            raise AppError("NETWORK_BLOCKED") from error
        binding = self.sessions.binding(url) if self.sessions else nullcontext({})
        with binding as context:
            return self.repository.enqueue("inspect", {"url": url, **context}, idempotency_key(key))

    def download(self, request, key=None):
        key = idempotency_key(key)
        data = request.model_dump()
        digest = request_hash("download", data)
        old = self.repository.replay(key, digest) if key else None
        if old:
            return old
        inspection = self.repository.get(request.inspection_id)
        if inspection["kind"] != "inspect" or inspection["state"] != "completed":
            raise AppError("CONFLICT")
        metadata, expires = self.repository.inspection(request.inspection_id)
        if expires <= time.time():
            raise AppError("INSPECTION_EXPIRED")
        selected = None
        available = metadata["choices"]
        if request.policy == "choice":
            matching = [choice for choice in available if choice["choice_id"] == request.choice_id]
            if len(matching) != 1:
                raise AppError("QUALITY_CHANGED")
            selected = matching[0]
            possible = matching
        else:
            possible = [
                choice
                for choice in available
                if choice["audio_available"]
                and (request.policy == "best" or choice["quality_class"] <= 1080)
            ]
        if not possible or not all(choice["audio_available"] for choice in possible):
            raise AppError("UNKNOWN_RESOLUTION")
        effective = max(possible, key=lambda choice: choice["quality_class"])
        estimated = metadata.get("estimates", {}).get(effective["choice_id"])
        self.destinations.check(request.folder_id, estimated)
        payload = {
            "url": inspection["payload"]["url"],
            "choice": selected,
            "policy": request.policy,
            "inspection_id": request.inspection_id,
            "folder_id": request.folder_id,
            "estimated_bytes": estimated,
            "title": metadata.get("title", "Film"),
        }
        binding = (
            self.sessions.binding(
                payload["url"],
                expected=inspection["payload"].get("session_ref"),
                revision=inspection["payload"].get("session_revision", 0),
            )
            if self.sessions
            else nullcontext({})
        )
        with binding as context:
            payload.update(context)
            return self.repository.enqueue(
                "download", payload, key, request_hash=digest, inspection_id=request.inspection_id
            )

    def retry(self, job_id, key=None):
        key = idempotency_key(key)
        digest = hashlib.sha256(("retry:" + job_id).encode()).hexdigest()
        if key:
            replay = self.repository.replay(key, digest)
            if replay:
                return replay
        # Serialize retry acceptance against deletion of its source partials.
        with self.artifacts.lock:
            job = self.repository.get(job_id)
            self.destinations.check(
                job["payload"].get("folder_id", "default"), job["payload"].get("estimated_bytes")
            )
            binding = (
                self.sessions.binding(
                    job["payload"]["url"], expected=job["payload"].get("session_ref")
                )
                if self.sessions
                else nullcontext({})
            )
            with binding:
                return self.repository.retry(job_id, key)

    def snapshot(self, job_id, *, inspection_only=False):
        job = self.repository.get(job_id)
        if inspection_only and job["kind"] != "inspect":
            raise KeyError(job_id)
        response = {
            key: job[key]
            for key in ("id", "kind", "state", "phase", "progress", "error", "created", "updated")
        }
        response["title"] = job["payload"].get("title", "Memeriksa tautan")
        if job["state"] == "completed" and job["kind"] == "inspect":
            metadata, expires = self.repository.inspection(job_id)
            response.update(metadata=metadata, expires_at=expires, expired=time.time() >= expires)
            response["title"] = metadata.get("title", "Film")
        if job["kind"] == "download":
            response["attempt"] = job["payload"].get("attempt", 1)
            response["retry_of"] = job["payload"].get("retry_of")
            reference = job["payload"].get("session_ref")
            stale = bool(reference and (not self.sessions or not self.sessions.accepts(reference)))
            response["needs_reinspection"] = stale
            response["inspection_id"] = job["payload"].get("inspection_id")
            response["can_retry"] = self.repository.retry_available(job_id) and not stale
            response["storage"] = self.artifacts.storage_summary(job)
            response["resumed"] = bool((job["result"] or {}).get("resumed"))
            response["requested_quality"] = {
                "policy": job["payload"].get("policy", "max_1080"),
                "choice": job["payload"].get("choice"),
            }
            response["effective_quality"] = (job["result"] or {}).get("effective_quality")
            files = []
            for record in self.repository.artifacts_for(job_id):
                try:
                    path, _, _ = self.artifacts.locate(record["id"])
                    available = True
                    name = path.name
                except (AppError, OSError):
                    available, name = False, "File tidak tersedia"
                files.append(
                    {
                        "id": record["id"],
                        "name": name,
                        "size_bytes": record["size_bytes"],
                        "available": available,
                    }
                )
            response["files"] = files
        return response
