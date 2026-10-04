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


def test_invalid_batch_and_limit() -> None:
    with TestClient(app) as client:
        missing = client.get("/api/batches/../secret")
        assert missing.status_code == 404
        too_many = client.post("/api/batches", json={"job_ids": [f"00000000-0000-4000-8000-00000000000{i}" for i in range(11)]})
        assert too_many.status_code == 422
        raw = client.post("/api/batches", json={"job_ids": ["00000000-0000-4000-8000-000000000001"], "ffmpeg_args": ["-i"]})
        assert raw.status_code in {404, 422}
