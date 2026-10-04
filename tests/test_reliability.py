"""Reliability checks for recovery, readiness, and deployment files."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import app
from app.services.storage import LocalStorage
from app.workers.conversion_worker import recover_orphans


def test_ready_and_invalid_configuration() -> None:
    with TestClient(app) as client:
        health = client.get("/api/health")
        ready = client.get("/api/ready")
    assert health.status_code == 200
    assert ready.status_code == 200
    assert ready.json()["ffmpeg"] is True
    assert "redis" in ready.json()
    with pytest.raises(ValidationError):
        Settings(WORKER_CONCURRENCY=0)
    with pytest.raises(ValidationError):
        Settings(REDIS_URL="http://example")


def test_orphan_processing_job_is_failed_and_not_downloadable(tmp_path: Path) -> None:
    storage = LocalStorage(Settings(TEMP_DIRECTORY=str(tmp_path), MIN_FREE_DISK_SPACE_BYTES=1))
    job = storage.create_job("clip.mp4")
    storage.write_metadata(job, {"job_id": job.job_id, "status": "processing", "conversion": {"preset": "h264_main"}})
    partial = job.directory / "output.partial.mp4"
    partial.write_bytes(b"partial")
    recover_orphans(storage, _Queue())
    payload = storage.read_metadata(job.job_id)
    assert payload["status"] == "failed"
    assert not partial.exists()


def test_deployment_files_exist() -> None:
    root = Path("Dockerfile")
    compose = Path("docker-compose.yml").read_text()
    assert root.exists()
    assert "ffmpeg" in root.read_text()
    assert "videoforge-api" not in compose
    assert "redis:" in compose
    assert "worker:" in compose


class _Queue:
    def enqueue(self, job_id: str, root: str) -> None:
        self.job_id = job_id
