"""Real Phase 6 conversions on a small generated video."""

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import settings_dependency
from app.main import app

SOURCE = Path("/tmp/video-converter-test-fixtures/quality-720.mp4")


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        TEMP_DIRECTORY=str(tmp_path),
        MAX_UPLOAD_SIZE_BYTES=8 * 1024 * 1024,
        MIN_FREE_DISK_SPACE_BYTES=1024,
        CONVERSION_TIMEOUT_SECONDS=180,
    )


def _video() -> Path:
    if SOURCE.exists():
        return SOURCE
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required")
    SOURCE.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "main",
            "-c:a", "aac", "-ac", "2", "-shortest", str(SOURCE),
        ],
        check=True,
        capture_output=True,
    )
    return SOURCE


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
    deadline = time.time() + 60
    body = {}
    while time.time() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in {"completed", "failed"}:
            return body
        time.sleep(0.1)
    raise AssertionError(body)


def test_validate_and_crf_conversion(client: TestClient, tmp_path: Path) -> None:
    job_id = _upload(client)
    preview = client.post(
        f"/api/jobs/{job_id}/convert/validate",
        json={"preset": "h264_main", "quality_preset": "balanced", "audio_bitrate": "128k"},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["valid"] is True
    assert preview.json()["crf"] == 23
    assert preview.json()["estimate"]["estimate"] is True
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "uploaded"
    started = client.post(
        f"/api/jobs/{job_id}/convert",
        json={"preset": "h264_main", "quality_preset": "balanced", "audio_bitrate": "128k"},
    )
    assert started.status_code == 200, started.text
    body = _wait(client, job_id)
    assert body["status"] == "completed", body
    assert body["output"]["compression"]["compression_percent"] is not None
    probed = _probe(tmp_path / job_id / "output.mp4")
    video = next(item for item in probed["streams"] if item["codec_type"] == "video")
    audio = next(item for item in probed["streams"] if item["codec_type"] == "audio")
    assert video["codec_name"] == "h264"
    assert video["profile"] == "Main"
    assert video["width"] == 1280
    assert video["height"] == 720
    assert audio["codec_name"] == "aac"


def test_bitrate_downscale_and_no_audio(client: TestClient, tmp_path: Path) -> None:
    job_id = _upload(client)
    rejected = client.post(
        f"/api/jobs/{job_id}/convert/validate",
        json={"preset": "h264_main", "resolution": "1080p"},
    )
    assert rejected.status_code == 422
    assert "upscale" in rejected.json()["detail"]
    started = client.post(
        f"/api/jobs/{job_id}/convert",
        json={
            "preset": "h264_main",
            "quality_mode": "bitrate",
            "video_bitrate": "1M",
            "resolution": "480p",
            "fps": "30",
            "audio_mode": "none",
        },
    )
    assert started.status_code == 200, started.text
    body = _wait(client, job_id)
    assert body["status"] == "completed", body
    probed = _probe(tmp_path / job_id / "output.mp4")
    video = next(item for item in probed["streams"] if item["codec_type"] == "video")
    assert video["height"] == 480
    assert video["codec_name"] == "h264"
    assert not any(item["codec_type"] == "audio" for item in probed["streams"])


def test_target_size_and_hevc_crf(client: TestClient) -> None:
    job_id = _upload(client)
    too_small = client.post(
        f"/api/jobs/{job_id}/convert",
        json={"preset": "h264_main", "quality_mode": "target_size", "target_size_bytes": 1000},
    )
    assert too_small.status_code == 422
    assert "too small" in too_small.json()["detail"]
    started = client.post(
        f"/api/jobs/{job_id}/convert",
        json={"preset": "h264_main", "quality_mode": "target_size", "target_size_bytes": 400000},
    )
    assert started.status_code == 200, started.text
    assert _wait(client, job_id)["status"] == "completed"
    catalog = client.get("/api/capabilities").json()["presets"]
    if not catalog["h265_hevc"]["available"]:
        return
    hevc_job = _upload(client)
    hevc = client.post(
        f"/api/jobs/{hevc_job}/convert",
        json={"preset": "h265_hevc", "quality_mode": "crf", "crf": 28, "audio_bitrate": "128k"},
    )
    assert hevc.status_code == 200, hevc.text
    assert _wait(client, hevc_job)["status"] == "completed"


def _probe(path: Path) -> dict:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)
