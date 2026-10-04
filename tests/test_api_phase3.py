"""Phase 3 H.264 Main conversion tests. Fixtures are generated, not committed."""

import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import capabilities_dependency, settings_dependency
from app.main import app
from app.services.storage import LocalStorage
from tests.test_command_builder import _capabilities

TINY = Path("/tmp/video-converter-test-fixtures/tiny.mp4")


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "TEMP_DIRECTORY": str(tmp_path),
        "MAX_UPLOAD_SIZE_BYTES": 5 * 1024 * 1024,
        "MIN_FREE_DISK_SPACE_BYTES": 1024,
        "CONVERSION_TIMEOUT_SECONDS": 120,
    }
    values.update(overrides)
    return Settings(**values)


def _video() -> Path:
    if TINY.exists():
        return TINY
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required")
    TINY.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "main",
            "-c:a", "aac", "-ac", "2", "-shortest", str(TINY),
        ],
        check=True,
        capture_output=True,
    )
    return TINY


@pytest.fixture
def client(tmp_path: Path):
    app.dependency_overrides[settings_dependency] = lambda: _settings(tmp_path)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _upload(client: TestClient) -> str:
    with _video().open("rb") as handle:
        response = client.post("/api/jobs", files={"file": ("clip.mp4", handle, "video/mp4")})
    assert response.status_code == 200, response.text
    return response.json()["job_id"]


def _wait(client: TestClient, job_id: str) -> dict:
    deadline = time.time() + 30
    body = {}
    while time.time() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in {"completed", "failed"}:
            return body
        time.sleep(0.1)
    raise AssertionError(body)


def test_h264_main_conversion_and_download(client: TestClient, tmp_path: Path) -> None:
    job_id = _upload(client)
    started = client.post(f"/api/jobs/{job_id}/convert", json={"preset": "h264_main"})
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "queued"
    body = _wait(client, job_id)
    assert body["status"] == "completed", body
    assert body["output"]["filename"] == "output.mp4"
    assert body["output"]["size_bytes"] > 0
    output = tmp_path / job_id / "output.mp4"
    assert output.is_file()
    assert not (tmp_path / job_id / "output.mp4.copy").exists()
    probed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    import json
    document = json.loads(probed.stdout)
    streams = document["streams"]
    video = next(item for item in streams if item["codec_type"] == "video")
    audio = next(item for item in streams if item["codec_type"] == "audio")
    assert video["codec_name"] == "h264"
    assert video["profile"] == "Main"
    assert video["pix_fmt"] == "yuv420p"
    assert audio["codec_name"] == "aac"
    assert audio["channels"] == 2
    assert "mp4" in document["format"]["format_name"]

    downloaded = client.get(f"/api/jobs/{job_id}/download")
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith("video/mp4")
    assert len(downloaded.content) == body["output"]["size_bytes"]


def test_convert_rejects_missing_encoder_injection_and_bad_jobs(client: TestClient, tmp_path: Path) -> None:
    missing = client.post(
        "/api/jobs/00000000-0000-4000-8000-000000000099/convert",
        json={"preset": "h264_main"},
    )
    assert missing.status_code == 404
    injected = client.post("/api/jobs/00000000-0000-4000-8000-000000000099/convert", json={
        "preset": "h264_main",
        "output_path": "/tmp/evil.mp4",
    })
    assert injected.status_code == 422

    job_id = _upload(client)
    app.dependency_overrides[capabilities_dependency] = lambda: _capabilities(["h264_v4l2m2m"], ["aac"])
    unavailable = client.post(f"/api/jobs/{job_id}/convert", json={"preset": "h264_main"})
    assert unavailable.status_code == 422
    assert "libx264" in unavailable.json()["detail"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "uploaded"
    app.dependency_overrides.pop(capabilities_dependency, None)


def test_conversion_failures_and_download_guards(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    job_id = _upload(client)
    accepted = client.post(f"/api/jobs/{job_id}/convert", json={"preset": "h264_main"})
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "queued"
    early = client.get(f"/api/jobs/{job_id}/download")
    assert early.status_code == 409
    _wait(client, job_id)
    storage_status = tmp_path / job_id / "metadata.json"
    import json
    payload = json.loads(storage_status.read_text())
    payload["status"] = "failed"
    storage_status.write_text(json.dumps(payload))

    def fail(command, timeout, duration, on_progress, should_cancel):
        return subprocess.CompletedProcess(command, 1, "", "boom")

    monkeypatch.setattr("app.services.converter._run_ffmpeg", fail)
    assert client.post(f"/api/jobs/{job_id}/convert", json={"preset": "h264_main"}).status_code == 200
    failed = _wait(client, job_id)
    assert failed["status"] == "failed"
    assert failed["error"] == "Conversion failed."
    assert client.get(f"/api/jobs/{job_id}/download").status_code == 409
    assert not (tmp_path / job_id / "output.mp4").exists()

    def timeout(command, timeout, duration, on_progress, should_cancel):
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr("app.services.converter._run_ffmpeg", timeout)
    client.post(f"/api/jobs/{job_id}/convert", json={"preset": "h264_main"})
    assert _wait(client, job_id)["error"] == "Conversion timed out."

    def empty(command, timeout, duration, on_progress, should_cancel):
        Path(command[-1]).write_bytes(b"")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("app.services.converter._run_ffmpeg", empty)
    client.post(f"/api/jobs/{job_id}/convert", json={"preset": "h264_main"})
    assert _wait(client, job_id)["error"] == "Conversion failed."
    assert not (tmp_path / job_id / "output.mp4").exists()

    monkeypatch.setattr(LocalStorage, "free_bytes", lambda self: 10)
    blocked = client.post(f"/api/jobs/{job_id}/convert", json={"preset": "h264_main"})
    assert blocked.status_code == 507
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "failed"
    assert client.get(f"/api/jobs/not-a-job/download").status_code == 404
