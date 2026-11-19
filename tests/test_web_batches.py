"""P5 atomic capacity, independent child outcomes and per-film recovery."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from test_integration import media_server as media_server
from test_web_flow import flow_app as flow_app
from test_web_flow import flow_client as flow_client
from test_web_runner import wait_for
from test_web_subtitles import subtitle_client as subtitle_client

from idlix_downloader.web.errors import AppError
from idlix_downloader.web.repository import Repository

BASE = "http://source.example/master.m3u8"


def wait_batch(app, ident):
    jobs = app.state.repository.batch(ident)
    for job in jobs:
        wait_for(
            app.state.repository,
            job,
            {"completed", "partial", "failed", "cancelled", "interrupted"},
            timeout=30,
        )
    return app.state.service.batch_snapshot(ident)


def test_batch_failure_isolated_and_policy_applied_per_movie(flow_client):
    client, app, *_ = flow_client
    response = client.post(
        "/api/batches",
        json={
            "urls": [
                BASE + "?one=SOURCE_SECRET",
                "http://source.example/missing.m3u8",
                BASE + "?two=2",
            ],
            "policy": "max_1080",
            "subtitle_mode": "off",
        },
    )
    assert response.status_code == 202, response.text
    batch = wait_batch(app, response.json()["id"])
    assert (
        batch["total"] == 3 and batch["counts"]["completed"] == 2 and batch["counts"]["failed"] == 1
    )
    assert batch["state"] == "partial"
    assert all(
        job["effective_quality"]["quality_class"] == 1080
        for job in batch["jobs"]
        if job["state"] == "completed"
    )
    assert "SOURCE_SECRET" not in json.dumps(batch)
    best = client.post("/api/batches", json={"urls": [BASE], "policy": "best"})
    assert (
        wait_batch(app, best.json()["id"])["jobs"][0]["effective_quality"]["quality_class"] == 2160
    )


def test_validation_dedup_replay_and_exact_capacity_are_atomic(flow_client):
    client, app, *_ = flow_client
    app.state.runner.stop()
    repo = app.state.repository
    assert (
        client.post(
            "/api/batches", json={"urls": [BASE, "ftp://source.example/bad.mp4"]}
        ).status_code
        == 422
    )
    assert (
        client.post("/api/batches", json={"urls": [BASE, "http://127.0.0.1/bad.mp4"]}).status_code
        == 403
    )
    assert client.post("/api/batches", json={"urls": [BASE] * 21}).status_code == 422
    assert repo.recent_batches() == [] and repo.recent() == []
    body = {"urls": [BASE, BASE, BASE], "policy": "max_1080"}
    first = client.post("/api/batches", json=body, headers={"Idempotency-Key": "batch-click"})
    assert first.status_code == 202
    ident = first.json()["id"]
    assert len(repo.batch(ident)) == 1
    client.post(f"/api/batches/{ident}/cancel")
    assert (
        client.post("/api/batches", json=body, headers={"Idempotency-Key": "batch-click"}).json()[
            "id"
        ]
        == ident
    )
    assert (
        client.post(
            "/api/batches",
            json={**body, "policy": "best"},
            headers={"Idempotency-Key": "batch-click"},
        ).status_code
        == 409
    )
    for _ in range(19):
        repo.enqueue("download", {})
    failed = client.post("/api/batches", json={"urls": [BASE + "?a", BASE + "?b"]})
    assert failed.status_code == 429
    assert len(repo.recent_batches()) == 1
    accepted = client.post("/api/batches", json={"urls": [BASE + "?last"]})
    assert accepted.status_code == 202 and len(repo.active_ids()) == 20
    assert len(repo.recent_batches()) == 2  # Parents do not consume worker capacity.


def test_concurrent_batches_cannot_oversubscribe(tmp_path):
    repo = Repository(tmp_path)
    barrier = Barrier(2)

    def create(index):
        barrier.wait()
        try:
            return repo.enqueue_batch(
                [{"url": BASE} for _ in range(12)], f"click-{index}", f"hash-{index}"
            )
        except AppError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, range(2)))
    assert results.count("QUEUE_FULL") == 1
    assert len(repo.active_ids()) == 12 and len(repo.recent_batches()) == 1


def test_batch_cancel_and_individual_retry_update_same_item(flow_client):
    client, app, *_ = flow_client
    created = client.post(
        "/api/batches", json={"urls": [BASE + "?one", BASE + "?two", BASE + "?three"]}
    )
    ident = created.json()["id"]
    original = app.state.repository.batch(ident)
    wait_for(app.state.repository, original[0], {"running", "completed"})
    assert client.post(f"/api/batches/{ident}/cancel").status_code == 200
    batch = wait_batch(app, ident)
    assert batch["state"] == "cancelled"
    retried = client.post(
        f"/api/jobs/{original[1]}/retry", headers={"Idempotency-Key": "retry-film"}
    )
    assert retried.status_code == 202
    batch = wait_batch(app, ident)
    assert (
        batch["total"] == 3
        and batch["counts"]["completed"] == 1
        and batch["counts"]["cancelled"] == 2
    )
    assert (
        batch["jobs"][1]["id"] == retried.json()["id"]
        and batch["jobs"][1]["retry_of"] == original[1]
    )
    reopened = Repository(app.state.repository.root)
    assert reopened.batch(ident)[1] == retried.json()["id"]


def test_explicit_missing_language_is_partial_and_next_movie_still_completes(subtitle_client):
    client, app, root, *_ = subtitle_client
    response = client.post(
        "/api/batches",
        json={
            "urls": [BASE + "?no-sub", BASE + "?with-sub"],
            "subtitle_mode": "embed",
            "subtitle_language": "id",
        },
    )
    assert response.status_code == 202
    batch = wait_batch(app, response.json()["id"])
    assert batch["jobs"][0]["state"] == "partial"
    assert batch["jobs"][1]["state"] == "completed"
    assert batch["counts"]["partial"] == 1 and batch["counts"]["completed"] == 1
