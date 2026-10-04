"""API tests for Phase 1 health and capabilities."""

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.capabilities import (
    BinaryStatus,
    CapabilitiesResponse,
    CapabilitySummary,
    CodecAvailability,
    HardwareAcceleration,
)


def _unavailable(label: str) -> CodecAvailability:
    return CodecAvailability(
        available=False,
        encoders=[],
        preferred_encoder=None,
        note=f"{label} encoder is not available in the installed FFmpeg",
    )


def test_health_and_capabilities_use_real_probe() -> None:
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        body = health.json()
        assert body["service"] == "video-converter"
        assert body["phase"] == "1-capability-detection"
        assert body["ffmpeg_available"] is True
        assert body["ffprobe_available"] is True
        assert body["ffmpeg_version"]
        assert body["status"] == "ok"
        assert body["h264_available"] is True
        assert body["aac_available"] is True
        assert body["hevc_available"] is True

        capabilities = client.get("/api/capabilities")
        assert capabilities.status_code == 200
        payload = capabilities.json()
        video_names = {item["name"] for item in payload["video_encoders"]}
        audio_names = {item["name"] for item in payload["audio_encoders"]}
        assert "libx264" in video_names
        assert "libx265" in video_names
        assert "aac" in audio_names
        assert "not_a_real_encoder" not in video_names
        assert payload["summary"]["h264"]["available"] is True
        assert payload["summary"]["h264"]["preferred_encoder"] == "libx264"
        assert payload["summary"]["aac"]["preferred_encoder"] == "aac"
        assert payload["summary"]["hevc"]["available"] is True
        assert payload["hardware_acceleration"]["methods"]
        assert "yuv420p" in {item["name"] for item in payload["pixel_formats"]}
        assert "mp4" in {item["name"] for item in payload["muxers"]}


def test_capabilities_do_not_invent_encoders_when_probe_is_empty() -> None:
    empty = CapabilitiesResponse(
        summary=CapabilitySummary(
            ffmpeg_available=False,
            ffprobe_available=False,
            video_encoder_count=0,
            audio_encoder_count=0,
            subtitle_encoder_count=0,
            video_decoder_count=0,
            audio_decoder_count=0,
            muxer_count=0,
            demuxer_count=0,
            pixel_format_count=0,
            h264=_unavailable("H.264 / AVC"),
            hevc=_unavailable("H.265 / HEVC"),
            aac=_unavailable("AAC"),
            hardware_acceleration=HardwareAcceleration(
                available=False,
                methods=[],
                hardware_video_encoders=[],
                note="No hardware acceleration detected; CPU encoding remains available",
            ),
        ),
        ffmpeg=BinaryStatus(name="ffmpeg", available=False, error="ffmpeg is not available on this server"),
        ffprobe=BinaryStatus(name="ffprobe", available=False, error="ffprobe is not available on this server"),
        video_encoders=[],
        audio_encoders=[],
        subtitle_encoders=[],
        video_decoders=[],
        audio_decoders=[],
        subtitle_decoders=[],
        muxers=[],
        demuxers=[],
        pixel_formats=[],
        hardware_acceleration=HardwareAcceleration(
            available=False,
            methods=[],
            hardware_video_encoders=[],
            note="No hardware acceleration detected; CPU encoding remains available",
        ),
    )
    app.dependency_overrides.clear()
    with TestClient(app) as client:
        client.app.state.capabilities = empty
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "degraded"
        assert health.json()["h264_available"] is False
        assert health.json()["aac_available"] is False
        assert health.json()["hevc_available"] is False
        capabilities = client.get("/api/capabilities")
        assert capabilities.json()["video_encoders"] == []
        assert capabilities.json()["summary"]["hevc"]["available"] is False
