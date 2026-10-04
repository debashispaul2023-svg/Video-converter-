"""Frontend contract tests. The browser does not encode video."""

from fastapi.testclient import TestClient

from app.main import app


def test_frontend_is_served_and_does_not_encode() -> None:
    with TestClient(app) as client:
        page = client.get("/")
        css = client.get("/static/css/app.css")
        script = client.get("/static/js/app.js")
        api = client.get("/static/js/api.js")
    assert page.status_code == 200
    assert "VideoForge" in page.text
    assert "Choose video" in page.text
    assert "ffmpeg.wasm" not in page.text.lower()
    assert "FFmpeg arguments" not in page.text
    assert css.status_code == 200
    assert "@media (min-width: 800px)" in css.text
    assert script.status_code == 200
    combined = script.text + api.text
    assert "EventSource" in combined
    assert "/api" in combined
    assert "jobs" in combined
    assert "convert/validate" in combined
    assert "ffmpeg.wasm" not in combined.lower()
    assert "20 GiB" in api.text
