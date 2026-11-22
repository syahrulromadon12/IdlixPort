"""Small transactional queue. Each operation owns its SQLite connection."""

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .errors import AppError

MAX_ATTEMPTS = 3

TERMINAL = {"completed", "failed", "cancelled", "interrupted", "partial", "paused"}


class Repository:
    def __init__(self, directory, limit=20):
        self.root, self.limit = Path(directory).resolve(), limit
        if self.root.exists() and not (self.root / "jobs.sqlite3").exists():
            if any(item.name != ".DS_Store" for item in self.root.iterdir()):
                raise AppError("STORAGE_UNAVAILABLE")
        if (self.root / "jobs.sqlite3").is_symlink():
            raise AppError("STORAGE_UNAVAILABLE")
        self.root.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            self.root.chmod(0o700)
        self.path = self.root / "jobs.sqlite3"
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise AppError("STORAGE_UNAVAILABLE")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            for statement in (
                (
                    "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, "
                    "payload TEXT NOT NULL, state TEXT NOT NULL, phase TEXT, progress TEXT NOT "
                    "NULL DEFAULT '{}', result TEXT, error TEXT, cancel_requested INTEGER NOT "
                    "NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL)"
                ),
                (
                    "CREATE TABLE IF NOT EXISTS inspections (job_id TEXT PRIMARY KEY REFERENCES "
                    "jobs(id), metadata TEXT NOT NULL, expires REAL NOT NULL)"
                ),
                (
                    "CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, job_id TEXT NOT "
                    "NULL REFERENCES jobs(id), relative_path TEXT NOT NULL, size_bytes INTEGER "
                    "NOT NULL)"
                ),
                (
                    "CREATE TABLE IF NOT EXISTS idempotency_keys (key TEXT PRIMARY KEY, "
                    "request_hash TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES jobs(id))"
                ),
            ):
                db.execute(statement)
            # Additive metadata: old schema-1 databases remain readable on rollback.
            db.execute(
                "CREATE TABLE IF NOT EXISTS attempts (job_id TEXT PRIMARY KEY REFERENCES jobs(id), "
                "root_id TEXT NOT NULL, parent_id TEXT UNIQUE, number INTEGER NOT NULL, "
                "UNIQUE(root_id,number))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, created REAL NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS batch_items (batch_id TEXT REFERENCES batches(id), "
                "position INTEGER, original_job_id TEXT REFERENCES jobs(id), "
                "current_job_id TEXT REFERENCES jobs(id), PRIMARY KEY(batch_id,position))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS batch_keys (key TEXT PRIMARY KEY, request_hash TEXT, "
                "batch_id TEXT REFERENCES batches(id))"
            )
            db.execute("PRAGMA user_version=1")
        if os.name != "nt":
            self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def enqueue(self, kind, payload, key=None, *, request_hash=None, inspection_id=None):
        if kind not in ("inspect", "download"):
            raise ValueError("Unknown job kind")
        data = json.dumps(payload, sort_keys=True, allow_nan=False)
        digest = request_hash or hashlib.sha256((kind + data).encode()).hexdigest()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if key:
                old = db.execute("SELECT * FROM idempotency_keys WHERE key=?", (key,)).fetchone()
                if old:
                    if old["request_hash"] != digest:
                        raise AppError("CONFLICT")
                    return old["job_id"]
            if inspection_id:
                row = db.execute(
                    "SELECT expires FROM inspections WHERE job_id=?", (inspection_id,)
                ).fetchone()
                if row is None or row[0] <= time.time():
                    raise AppError("INSPECTION_EXPIRED")
            count = db.execute(
                "SELECT count(*) FROM jobs WHERE state IN ('queued','running','cancelling','pausing')"
            ).fetchone()[0]
            if count >= self.limit:
                raise AppError("QUEUE_FULL")
            job_id, now = uuid.uuid4().hex, time.time()
            db.execute(
                "INSERT INTO jobs(id,kind,payload,state,created,updated) "
                "VALUES(?,?,?,'queued',?,?)",
                (job_id, kind, data, now, now),
            )
            if key:
                db.execute("INSERT INTO idempotency_keys VALUES(?,?,?)", (key, digest, job_id))
        return job_id

    def get(self, job_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        result = dict(row)
        for field in ("payload", "progress", "result", "error"):
            result[field] = json.loads(result[field]) if result[field] else None
        return result

    def claim(self, kind):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM jobs WHERE kind=? AND state IN ('running','cancelling','pausing')", (kind,)
            ).fetchone():
                return None
            row = db.execute(
                "SELECT id FROM jobs WHERE kind=? AND state='queued' ORDER BY created,id LIMIT 1",
                (kind,),
            ).fetchone()
            if not row:
                return None
            db.execute(
                (
                    "UPDATE jobs SET state='running',phase='extracting',updated=? WHERE id=? "
                    "AND state='queued'"
                ),
                (time.time(), row[0]),
            )
        return self.get(row[0])

    def progress(self, job_id, data):
        with self.connection() as db:
            db.execute(
                "UPDATE jobs SET phase=?,progress=?,updated=? WHERE id=? AND state='running'",
                (data["phase"], json.dumps(data, allow_nan=False), time.time(), job_id),
            )

    def finish(self, job_id, state, *, result=None, error=None):
        if state not in TERMINAL:
            raise ValueError("Invalid terminal state")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state,cancel_requested,kind FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if not row or row["state"] not in ("running", "cancelling", "pausing"):
                raise AppError("CONFLICT")
            if row["state"] == "pausing" and state in {"failed", "interrupted"}:
                state, result, error = "paused", None, None
            if row["cancel_requested"]:
                state, result, error = "cancelled", None, AppError("CANCELLED").public()
            if state in ("completed", "partial") and result is not None:
                if row["kind"] == "inspect":
                    db.execute(
                        "INSERT INTO inspections VALUES(?,?,?)",
                        (job_id, json.dumps(result, allow_nan=False), time.time() + 600),
                    )
                else:
                    records = []
                    for artifact in result.get("artifacts", []):
                        artifact_id = uuid.uuid4().hex
                        db.execute(
                            "INSERT INTO artifacts VALUES(?,?,?,?)",
                            (
                                artifact_id,
                                job_id,
                                f"downloads/{job_id}/{artifact['name']}",
                                artifact["size_bytes"],
                            ),
                        )
                        records.append({"id": artifact_id, **artifact})
                    result = {**result, "artifacts": records}
            db.execute(
                "UPDATE jobs SET state=?,result=?,error=?,updated=? WHERE id=?",
                (
                    state,
                    json.dumps(result, allow_nan=False) if result else None,
                    json.dumps(error) if error else None,
                    time.time(),
                    job_id,
                ),
            )

    def cancel(self, job_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone():
                raise KeyError(job_id)
            db.execute(
                (
                    "UPDATE jobs SET cancel_requested=1,state=CASE WHEN state IN ('queued','paused') THEN "
                    "'cancelled' ELSE 'cancelling' END,updated=? WHERE id=? AND state IN "
                    "('queued','running','paused')"
                ),
                (time.time(), job_id),
            )

    def pause(self, job_id):
        with self.connection() as db:
            cursor = db.execute(
                "UPDATE jobs SET state='pausing',updated=? "
                "WHERE id=? AND kind='download' AND state='running' AND phase='downloading'",
                (time.time(), job_id),
            )
            if cursor.rowcount != 1:
                raise AppError("CONFLICT")

    def resume(self, job_id, session):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,payload FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row["state"] != "paused":
                raise AppError("CONFLICT")
            if db.execute(
                "SELECT count(*) FROM jobs WHERE state IN ('queued','running','cancelling','pausing')"
            ).fetchone()[0] >= self.limit:
                raise AppError("QUEUE_FULL")
            payload = {**json.loads(row["payload"]), **session, "resume_paused": True}
            db.execute(
                "UPDATE jobs SET state='queued',phase=NULL,progress='{}',error=NULL,payload=?,"
                "updated=? WHERE id=?",
                (json.dumps(payload, allow_nan=False), time.time(), job_id),
            )

    def recover(self):
        with self.connection() as db:
            db.execute(
                "UPDATE jobs SET state=CASE WHEN state='pausing' THEN 'paused' ELSE 'interrupted' END,updated=? "
                "WHERE state IN ('running','cancelling','pausing')",
                (time.time(),),
            )

    def backup(self, path):
        with self.connection() as source, sqlite3.connect(path) as target:
            source.backup(target)

    def replay(self, key, digest):
        with self.connection() as db:
            row = db.execute("SELECT * FROM idempotency_keys WHERE key=?", (key,)).fetchone()
        if row:
            if row["request_hash"] != digest:
                raise AppError("CONFLICT")
            return row["job_id"]
        return None

    def inspection(self, job_id):
        with self.connection() as db:
            row = db.execute(
                "SELECT metadata,expires FROM inspections WHERE job_id=?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return json.loads(row[0]), row[1]

    def recent(self):
        with self.connection() as db:
            return [
                row[0]
                for row in db.execute("SELECT id FROM jobs ORDER BY created DESC,id DESC LIMIT 20")
            ]

    def artifact(self, artifact_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if row is None:
            raise KeyError(artifact_id)
        return dict(row)

    def artifacts_for(self, job_id):
        with self.connection() as db:
            return [
                dict(row) for row in db.execute("SELECT * FROM artifacts WHERE job_id=?", (job_id,))
            ]

    def delete_artifact(self, artifact_id):
        with self.connection() as db:
            db.execute("DELETE FROM artifacts WHERE id=?", (artifact_id,))

    def retry(self, job_id, key=None):
        """Create one bounded descendant atomically, never mutate an old attempt."""
        digest = hashlib.sha256(("retry:" + job_id).encode()).hexdigest()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if key:
                old = db.execute("SELECT * FROM idempotency_keys WHERE key=?", (key,)).fetchone()
                if old:
                    if old["request_hash"] != digest:
                        raise AppError("CONFLICT")
                    return old["job_id"]
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["kind"] != "download" or row["state"] not in {
                "failed",
                "cancelled",
                "interrupted",
                "partial",
            }:
                raise AppError("CONFLICT")
            attempt = db.execute("SELECT * FROM attempts WHERE job_id=?", (job_id,)).fetchone()
            root, number = (attempt["root_id"], attempt["number"]) if attempt else (job_id, 1)
            latest = db.execute(
                "SELECT MAX(number) FROM attempts WHERE root_id=?", (root,)
            ).fetchone()[0]
            if latest and number != latest:
                raise AppError("CONFLICT")
            if number >= MAX_ATTEMPTS:
                raise AppError("RETRY_LIMIT")
            if (
                db.execute(
                    "SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running','cancelling','pausing')"
                ).fetchone()[0]
                >= self.limit
            ):
                raise AppError("QUEUE_FULL")
            payload = json.loads(row["payload"])
            payload.pop("resume_paused", None)
            payload.update(retry_of=job_id, root_job_id=root, attempt=number + 1)
            if row["state"] == "partial":
                payload["video_source"] = job_id
            ident, now = uuid.uuid4().hex, time.time()
            db.execute(
                "INSERT INTO jobs(id,kind,payload,state,created,updated) "
                "VALUES(?,'download',?,'queued',?,?)",
                (ident, json.dumps(payload, sort_keys=True, allow_nan=False), now, now),
            )
            db.execute("INSERT OR IGNORE INTO attempts VALUES(?,?,NULL,1)", (root, root))
            db.execute("INSERT INTO attempts VALUES(?,?,?,?)", (ident, root, job_id, number + 1))
            db.execute(
                "UPDATE batch_items SET current_job_id=? WHERE current_job_id=?", (ident, job_id)
            )
            if key:
                db.execute("INSERT INTO idempotency_keys VALUES(?,?,?)", (key, digest, ident))
            return ident

    def page(self, offset=0, limit=20):
        with self.connection() as db:
            total = db.execute("SELECT COUNT(*) FROM jobs WHERE kind='download'").fetchone()[0]
            ids = [
                row[0]
                for row in db.execute(
                    "SELECT id FROM jobs WHERE kind='download' "
                    "ORDER BY created DESC,id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]
        return ids, total

    def active_ids(self):
        with self.connection() as db:
            return [
                row[0]
                for row in db.execute(
                    "SELECT id FROM jobs WHERE state IN ('queued','running','cancelling','pausing') "
                    "ORDER BY created,id"
                )
            ]

    def retry_available(self, job_id):
        job = self.get(job_id)
        if job["kind"] != "download" or job["state"] not in {
            "failed",
            "cancelled",
            "interrupted",
            "partial",
        }:
            return False
        with self.connection() as db:
            row = db.execute("SELECT * FROM attempts WHERE job_id=?", (job_id,)).fetchone()
            if row and row["number"] >= MAX_ATTEMPTS:
                return False
            return (
                db.execute("SELECT 1 FROM attempts WHERE parent_id=?", (job_id,)).fetchone() is None
            )

    def clear_artifacts(self, job_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT result,state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row["state"] not in TERMINAL:
                raise AppError("CONFLICT")
            result = json.loads(row["result"]) if row["result"] else {}
            result.update(artifacts=[], files_deleted=True)
            db.execute("DELETE FROM artifacts WHERE job_id=?", (job_id,))
            db.execute(
                "UPDATE jobs SET result=?,updated=? WHERE id=?",
                (json.dumps(result), time.time(), job_id),
            )

    def invalidate_inspections(self):
        with self.connection() as db:
            db.execute("UPDATE inspections SET expires=0")

    def accept_video(self, job_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,result FROM jobs WHERE id=?", (job_id,)).fetchone()
            if (
                row
                and row["state"] == "completed"
                and json.loads(row["result"] or "{}")
                .get("subtitle_status", {})
                .get("accepted_without_subtitles")
            ):
                return
            if (
                not row
                or row["state"] != "partial"
                or db.execute("SELECT 1 FROM attempts WHERE parent_id=?", (job_id,)).fetchone()
            ):
                raise AppError("CONFLICT")
            result = json.loads(row["result"])
            result.update(
                partial=False, subtitle_status={"mode": "off", "accepted_without_subtitles": True}
            )
            db.execute(
                "UPDATE jobs SET state='completed',result=?,error=NULL,updated=? WHERE id=?",
                (json.dumps(result), time.time(), job_id),
            )

    def batch_replay(self, key, digest):
        with self.connection() as db:
            row = db.execute("SELECT * FROM batch_keys WHERE key=?", (key,)).fetchone()
        if row:
            if row["request_hash"] != digest:
                raise AppError("CONFLICT")
            return row["batch_id"]
        return None

    def enqueue_batch(self, payloads, key, digest):
        if not 1 <= len(payloads) <= 20:
            raise AppError("INVALID_INPUT")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if key:
                old = db.execute("SELECT * FROM batch_keys WHERE key=?", (key,)).fetchone()
                if old:
                    if old["request_hash"] != digest:
                        raise AppError("CONFLICT")
                    return old["batch_id"]
            count = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running','cancelling','pausing')"
            ).fetchone()[0]
            if count + len(payloads) > self.limit:
                raise AppError("QUEUE_FULL")
            batch, now = uuid.uuid4().hex, time.time()
            db.execute("INSERT INTO batches VALUES(?,?)", (batch, now))
            for position, payload in enumerate(payloads):
                ident = uuid.uuid4().hex
                data = {**payload, "batch_id": batch, "batch_position": position}
                db.execute(
                    "INSERT INTO jobs(id,kind,payload,state,created,updated) "
                    "VALUES(?,'download',?,'queued',?,?)",
                    (ident, json.dumps(data, allow_nan=False), now + position * 0.000001, now),
                )
                db.execute(
                    "INSERT INTO batch_items VALUES(?,?,?,?)", (batch, position, ident, ident)
                )
            if key:
                db.execute("INSERT INTO batch_keys VALUES(?,?,?)", (key, digest, batch))
            return batch

    def batch(self, batch_id):
        with self.connection() as db:
            if not db.execute("SELECT 1 FROM batches WHERE id=?", (batch_id,)).fetchone():
                raise KeyError(batch_id)
            return [
                row[0]
                for row in db.execute(
                    "SELECT current_job_id FROM batch_items WHERE batch_id=? ORDER BY position",
                    (batch_id,),
                )
            ]

    def recent_batches(self):
        with self.connection() as db:
            return [
                row[0]
                for row in db.execute(
                    "SELECT id FROM batches ORDER BY created DESC,id DESC LIMIT 20"
                )
            ]

    def cancel_batch(self, batch_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM batches WHERE id=?", (batch_id,)).fetchone():
                raise KeyError(batch_id)
            db.execute(
                "UPDATE jobs SET cancel_requested=1,state=CASE WHEN state IN ('queued','paused') THEN "
                "'cancelled' ELSE 'cancelling' END,updated=? "
                "WHERE id IN (SELECT current_job_id FROM batch_items WHERE batch_id=?) "
                "AND state IN ('queued','running','paused')",
                (time.time(), batch_id),
            )

    def can_accept_video(self, job_id):
        with self.connection() as db:
            return (
                db.execute("SELECT 1 FROM attempts WHERE parent_id=?", (job_id,)).fetchone() is None
            )

    def batch_counts(self, batch_id):
        with self.connection() as db:
            return {
                row[0]: row[1]
                for row in db.execute(
                    "SELECT jobs.state,COUNT(*) FROM batch_items "
                    "JOIN jobs ON current_job_id=jobs.id "
                    "WHERE batch_id=? GROUP BY jobs.state",
                    (batch_id,),
                )
            }
