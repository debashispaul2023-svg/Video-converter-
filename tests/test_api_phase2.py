"""Phase 2 upload and metadata API tests. Conversion is not covered."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import settings_dependency
from app.main import app
from app.services.ffprobe import FFProbeService, ProbeTimeout, ProbeUnavailable

TINY_DIR = Path("/tmp/video-converter-test-fixtures")


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "TEMP_DIRECTORY": str(tmp_path),
        "MAX_UPLOAD_SIZE_BYTES": 5 * 1024 * 1024,
        "MIN_FREE_DISK_SPACE_BYTES": 1024,
        "FFPROBE_TIMEOUT_SECONDS": 30,
        "UPLOAD_TIMEOUT_SECONDS": 21600,
    }
    values.update(overrides)
    return Settings(**values)


def _tiny_video(suffix: str) -> Path:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required to generate the tiny fixture")
    TINY_DIR.mkdir(parents=True, exist_ok=True)
    target = TINY_DIR / f"tiny{suffix}"
    if target.exists() and target.stat().st_size > 0:
        return target
    source = TINY_DIR / "tiny.mp4"
    if not source.exists():
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=160x120:rate=30",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000",
                "-t",
                "1",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-profile:v",
                "main",
                "-c:a",
                "aac",
                "-ac",
                "2",
                "-shortest",
                str(source),
            ],
            check=True,
            capture_output=True,
        )
    if suffix != ".mp4":
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(source), "-c", "copy", str(target)],
            check=True,
            capture_output=True,
        )
    return target if suffix != ".mp4" else source


@pytest.fixture
def client(tmp_path: Path):
    app.dependency_overrides[settings_dependency] = lambda: _settings(tmp_path)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_upload_mp4_and_fetch_job(client: TestClient, tmp_path: Path) -> None:
    video = _tiny_video(".mp4")
    with video.open("rb") as handle:
        response = client.post(
            "/api/jobs",
            files={"file": ("../../safe-name.mp4", handle, "application/octet-stream")},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "uploaded"
    assert body["file"]["filename"] == "safe-name.mp4"
    assert body["file"]["size_bytes"] > 0
    assert body["media"]["video"]["codec"] == "h264"
    assert body["media"]["video"]["profile"] == "Main"
    assert body["media"]["video"]["width"] == 160
    assert body["media"]["video"]["height"] == 120
    assert body["media"]["video"]["pixel_format"] == "yuv420p"
    assert body["media"]["video"]["fps"] == 30
    assert body["media"]["audio"]["codec"] == "aac"
    assert body["media"]["audio"]["sample_rate"] == 48000
    assert body["media"]["audio"]["channels"] == 2
    assert body["media"]["duration_seconds"] == pytest.approx(1.0, abs=0.1)
    assert "mov" in body["media"]["container"] or "mp4" in body["media"]["container"]
    assert "raw_probe" not in body
    assert "/tmp/" not in response.text
    job_dir = tmp_path / body["job_id"]
    assert (job_dir / "input.mp4").is_file()
    assert not (job_dir / "input.mp4.copy").exists()
    upload_state = json.loads((job_dir / "upload.json").read_text())
    assert upload_state["complete"] is True
    assert upload_state["uploaded_bytes"] == body["file"]["size_bytes"]
    assert upload_state["upload_id"]
    assert upload_state["chunk_size"] > 0
    assert not (tmp_path / "safe-name.mp4").exists()

    fetched = client.get(f"/api/jobs/{body['job_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["media"]["video"]["codec"] == "h264"


def test_upload_mkv(client: TestClient) -> None:
    video = _tiny_video(".mkv")
    with video.open("rb") as handle:
        response = client.post("/api/jobs", files={"file": ("clip.mkv", handle, "video/x-matroska")})
    assert response.status_code == 200, response.text
    assert response.json()["media"]["video"]["codec"] == "h264"
    assert "matroska" in response.json()["media"]["container"]


def test_invalid_empty_missing_and_oversized(client: TestClient, tmp_path: Path) -> None:
    invalid = client.post("/api/jobs", files={"file": ("notes.mp4", b"this is not media", "video/mp4")})
    assert invalid.status_code == 422
    assert invalid.json()["detail"] in {
        "Uploaded file is not a valid media file.",
        "Unable to analyze this media file.",
    }
    assert list(tmp_path.iterdir()) == []

    empty = client.post("/api/jobs", files={"file": ("empty.mp4", b"", "video/mp4")})
    assert empty.status_code == 422
    assert empty.json()["detail"] == "Uploaded file is empty."

    missing = client.post("/api/jobs")
    assert missing.status_code == 422

    unsupported = client.post("/api/jobs", files={"file": ("run.exe", b"MZ", "application/octet-stream")})
    assert unsupported.status_code == 422

    app.dependency_overrides[settings_dependency] = lambda: _settings(
        tmp_path, MAX_UPLOAD_SIZE_BYTES=32
    )
    oversized = client.post("/api/jobs", files={"file": ("big.mp4", b"0" * 64, "video/mp4")})
    assert oversized.status_code == 413
    assert oversized.json()["detail"] == (
        "Upload exceeds the server's maximum supported file size of 32 bytes."
    )
    assert list(tmp_path.iterdir()) == []


def test_unknown_and_traversing_job_ids_are_not_found(client: TestClient) -> None:
    assert client.get("/api/jobs/not-a-real-job").status_code == 404
    assert client.get("/api/jobs/../secret").status_code == 404
    assert client.get("/api/jobs/00000000-0000-4000-8000-000000000000").status_code == 404


def test_missing_ffprobe_and_timeout_clean_up(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    video = _tiny_video(".mp4")
    app.dependency_overrides[settings_dependency] = lambda: _settings(
        tmp_path, FFPROBE_BINARY="ffprobe-does-not-exist"
    )
    with video.open("rb") as handle:
        missing = client.post("/api/jobs", files={"file": ("clip.mp4", handle, "video/mp4")})
    assert missing.status_code == 503
    assert missing.json()["detail"] == "Media analysis service is unavailable."
    assert list(tmp_path.iterdir()) == []

    app.dependency_overrides[settings_dependency] = lambda: _settings(tmp_path)

    def explode(self, media_path: str, display_name: str, size_bytes: int):
        raise ProbeTimeout()

    monkeypatch.setattr(FFProbeService, "analyze", explode)
    with video.open("rb") as handle:
        timed_out = client.post("/api/jobs", files={"file": ("clip.mp4", handle, "video/mp4")})
    assert timed_out.status_code == 408
    assert timed_out.json()["detail"] == "Media analysis timed out."
    assert list(tmp_path.iterdir()) == []

    def unavailable(self, media_path: str, display_name: str, size_bytes: int):
        raise ProbeUnavailable()

    monkeypatch.setattr(FFProbeService, "analyze", unavailable)
    with video.open("rb") as handle:
        down = client.post("/api/jobs", files={"file": ("clip.mp4", handle, "video/mp4")})
    assert down.status_code == 503
    assert list(tmp_path.iterdir()) == []


def test_low_disk_and_upload_timeout_clean_up(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    from app.services.storage import LocalStorage

    monkeypatch.setattr(LocalStorage, "free_bytes", lambda self: 100)
    response = client.post("/api/jobs", files={"file": ("clip.mp4", b"1234", "video/mp4")})
    assert response.status_code == 507
    assert response.json()["detail"] == "The server does not have enough free storage for this upload."
    assert list(tmp_path.iterdir()) == []

    monkeypatch.undo()
    from app.services.storage import UploadTimeout

    async def timed_out_upload(self, upload, location):
        raise UploadTimeout()

    monkeypatch.setattr(LocalStorage, "save_upload", timed_out_upload)
    timed_out = client.post("/api/jobs", files={"file": ("clip.mp4", b"123456", "video/mp4")})
    assert timed_out.status_code == 408
    assert timed_out.json()["detail"] == "Upload timed out."
    assert list(tmp_path.iterdir()) == []
