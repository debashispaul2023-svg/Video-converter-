"""Real conversions for the Phase 4 presets. No large media is committed."""

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

TINY = Path("/tmp/video-converter-test-fixtures/tiny.mp4")


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        TEMP_DIRECTORY=str(tmp_path),
        MAX_UPLOAD_SIZE_BYTES=5 * 1024 * 1024,
        MIN_FREE_DISK_SPACE_BYTES=1024,
        CONVERSION_TIMEOUT_SECONDS=180,
    )


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


def _wait(client: TestClient, job_id: str) -> dict:
    deadline = time.time() + 60
    body = {}
    while time.time() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in {"completed", "failed"}:
            return body
        time.sleep(0.1)
    raise AssertionError(body)


@pytest.mark.parametrize(
    ("preset", "codec", "profiles", "pixel_format"),
    [
        ("h264_baseline", "h264", {"Baseline", "Constrained Baseline"}, "yuv420p"),
        ("h264_main", "h264", {"Main"}, "yuv420p"),
        ("h264_high", "h264", {"High"}, "yuv420p"),
        ("h264_high10", "h264", {"High 10", "High10"}, "yuv420p10le"),
        ("h265_hevc", "hevc", {"Main"}, "yuv420p"),
    ],
)
def test_preset_conversion_and_download(
    client: TestClient,
    tmp_path: Path,
    preset: str,
    codec: str,
    profiles: set[str],
    pixel_format: str,
) -> None:
    catalog = client.get("/api/capabilities").json()["presets"]
    if not catalog[preset]["available"]:
        pytest.skip(f"{preset} is not available on this FFmpeg build")
    with _video().open("rb") as handle:
        job_id = client.post("/api/jobs", files={"file": ("clip.mp4", handle, "video/mp4")}).json()["job_id"]
    started = client.post(f"/api/jobs/{job_id}/convert", json={"preset": preset})
    assert started.status_code == 200, started.text
    body = _wait(client, job_id)
    assert body["status"] == "completed", body
    downloaded = client.get(f"/api/jobs/{job_id}/download")
    assert downloaded.status_code == 200
    assert len(downloaded.content) == body["output"]["size_bytes"]
    probed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_streams", "-show_format", str(tmp_path / job_id / "output.mp4"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    document = json.loads(probed.stdout)
    video = next(item for item in document["streams"] if item["codec_type"] == "video")
    audio = next(item for item in document["streams"] if item["codec_type"] == "audio")
    assert video["codec_name"] == codec
    assert video["profile"] in profiles
    assert video["pix_fmt"] == pixel_format
    assert audio["codec_name"] == "aac"
    assert audio["channels"] == 2
    assert "mp4" in document["format"]["format_name"]
