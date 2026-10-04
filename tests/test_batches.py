"""Batch conversion tests. Child jobs stay independent."""

from fastapi.testclient import TestClient

from app.api.batches import _batch_status
from app.main import app


def test_batch_status_rules() -> None:
    assert _batch_status({"queued": 2, "processing": 0, "completed": 0, "failed": 0, "cancelled": 0}, 2) == "queued"
    assert _batch_status({"queued": 0, "processing": 1, "completed": 1, "failed": 0, "cancelled": 0}, 2) == "processing"
    assert _batch_status({"queued": 0, "processing": 0, "completed": 2, "failed": 0, "cancelled": 0}, 2) == "completed"
    assert _batch_status({"queued": 0, "processing": 0, "completed": 1, "failed": 1, "cancelled": 0}, 2) == "completed_with_errors"
    assert _batch_status({"queued": 0, "processing": 0, "completed": 0, "failed": 0, "cancelled": 2}, 2) == "cancelled"
    assert _batch_status({"queued": 0, "processing": 0, "completed": 0, "failed": 2, "cancelled": 0}, 2) == "failed"


def _ids(count: int) -> list[str]:
    return [f"00000000-0000-4000-8000-{index:012d}" for index in range(count)]


def test_batch_limits_are_enforced() -> None:
    with TestClient(app) as client:
        assert client.get("/api/batches/../secret").status_code == 404
        assert client.post("/api/batches", json={"job_ids": _ids(19)}).status_code == 422
        assert client.post("/api/batches", json={"job_ids": _ids(31)}).status_code == 422
        assert client.post("/api/batches", json={"job_ids": _ids(20)}).status_code == 404
        assert client.post("/api/batches", json={"job_ids": _ids(30)}).status_code == 404
        raw = client.post("/api/batches", json={"job_ids": _ids(20), "ffmpeg_args": ["-i"]})
        assert raw.status_code == 422
