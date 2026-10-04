"""Security and resource-limit tests."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import sanitize_filename
from app.main import app
from app.services.command_builder import build_preset_command
from app.config import Settings
from tests.test_command_builder import _capabilities


def test_job_id_and_filename_attacks() -> None:
    with TestClient(app) as client:
        assert client.get("/api/jobs/../secret").status_code == 404
        assert client.get("/api/jobs/not-a-uuid").status_code == 404
        assert client.get("/api/jobs/not-a-uuid/download").status_code == 404
    assert "/" not in sanitize_filename("../../etc/passwd")
    assert ".." not in sanitize_filename("..\\..\\secret.mp4")
    assert "\x00" not in sanitize_filename("bad\x00.mp4")


def test_settings_reject_injection_and_extreme_values() -> None:
    with TestClient(app) as client:
        injected = client.post("/api/jobs/00000000-0000-4000-8000-000000000000/convert", json={"preset": "h264_main", "vf": "scale=evil"})
        assert injected.status_code in {404, 422}
    settings = Settings()
    source = Path("input.mp4")
    output = Path("output.mp4")
    command, _plan = build_preset_command(
        settings,
        _capabilities(["libx264"], ["aac"]),
        "h264_main",
        source,
        output,
        has_audio=False,
    )
    assert isinstance(command, list)
    assert "shell" not in command
    text = Path("backend/app/services/converter.py").read_text()
    assert "shell=True" not in text


def test_security_headers_and_same_origin_default() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "access-control-allow-origin" not in {key.lower() for key in response.headers}
