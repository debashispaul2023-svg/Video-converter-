"""Command builder tests for the H.264 Main preset. No process is launched."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.schemas.capabilities import (
    BinaryStatus,
    CapabilitiesResponse,
    CapabilitySummary,
    CodecAvailability,
    HardwareAcceleration,
)
from app.schemas.jobs import ConvertRequest
from app.services.command_builder import EncoderUnavailable, build_h264_main_command


def _capabilities(h264: list[str], aac: list[str]) -> CapabilitiesResponse:
    def codec(names: list[str], label: str) -> CodecAvailability:
        return CodecAvailability(
            available=bool(names),
            encoders=names,
            preferred_encoder=names[0] if names else None,
            note=label,
        )

    hardware = HardwareAcceleration(
        available=False,
        methods=[],
        hardware_video_encoders=[],
        note="cpu",
    )
    return CapabilitiesResponse(
        summary=CapabilitySummary(
            ffmpeg_available=True,
            ffprobe_available=True,
            video_encoder_count=len(h264),
            audio_encoder_count=len(aac),
            subtitle_encoder_count=0,
            video_decoder_count=0,
            audio_decoder_count=0,
            muxer_count=1,
            demuxer_count=1,
            pixel_format_count=1,
            h264=codec(h264, "h264"),
            hevc=codec([], "hevc"),
            aac=codec(aac, "aac"),
            hardware_acceleration=hardware,
        ),
        ffmpeg=BinaryStatus(name="ffmpeg", available=True, version="test"),
        ffprobe=BinaryStatus(name="ffprobe", available=True, version="test"),
        video_encoders=[],
        audio_encoders=[],
        subtitle_encoders=[],
        video_decoders=[],
        audio_decoders=[],
        subtitle_decoders=[],
        muxers=[],
        demuxers=[],
        pixel_formats=[],
        hardware_acceleration=hardware,
    )


def test_h264_main_command_is_an_argument_list(tmp_path: Path) -> None:
    command = build_h264_main_command(
        Settings(),
        _capabilities(["libx264"], ["aac"]),
        tmp_path / "input.mp4",
        tmp_path / "output.mp4",
        has_audio=True,
    )
    assert isinstance(command, list)
    assert all(isinstance(part, str) for part in command)
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-profile:v") + 1] == "main"
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-ac") + 1] == "2"
    assert "+faststart" in command
    assert "-crf" in command
    assert "shell" not in command
    joined = " ".join(command)
    assert ";" not in joined
    assert "&&" not in joined


def test_video_only_command_does_not_invent_audio(tmp_path: Path) -> None:
    command = build_h264_main_command(
        Settings(),
        _capabilities(["libx264"], ["aac"]),
        tmp_path / "input.mp4",
        tmp_path / "output.mp4",
        has_audio=False,
    )
    assert "-an" in command
    assert "-c:a" not in command


def test_missing_libx264_is_not_replaced(tmp_path: Path) -> None:
    with pytest.raises(EncoderUnavailable, match="does not provide libx264"):
        build_h264_main_command(
            Settings(),
            _capabilities(["h264_vaapi"], ["aac"]),
            tmp_path / "input.mp4",
            tmp_path / "output.mp4",
            has_audio=True,
        )


def test_client_cannot_add_ffmpeg_arguments() -> None:
    with pytest.raises(ValidationError):
        ConvertRequest.model_validate({"preset": "h264_main", "ffmpeg": "-vf evil"})
    with pytest.raises(ValidationError):
        ConvertRequest.model_validate({"preset": "hevc"})


def test_timestamp_flags_are_conditional(tmp_path: Path) -> None:
    from app.services.command_builder import CommandError, build_preset_command

    valid = {
        "video": {"start_time_seconds": 0.0, "duration_seconds": 1.0, "timestamps_missing": False},
        "audio": {"codec": "aac", "start_time_seconds": 0.0, "duration_seconds": 1.0, "timestamps_missing": False},
    }
    command, _plan = build_preset_command(
        Settings(),
        _capabilities(["libx264"], ["aac"]),
        "h264_main",
        tmp_path / "input.mp4",
        tmp_path / "output.partial.mp4",
        has_audio=True,
        request=ConvertRequest(preset="h264_main", fps="30"),
        media=valid,
    )
    assert "+genpts" not in command
    assert "make_zero" not in command
    assert "aresample=async=1:first_pts=0" in command
    assert "-shortest" not in command
    missing, _plan = build_preset_command(
        Settings(),
        _capabilities(["libx264"], ["aac"]),
        "h264_main",
        tmp_path / "input.mp4",
        tmp_path / "output.partial.mp4",
        has_audio=True,
        media={"video": {"timestamps_missing": True}, "audio": {"codec": "aac"}},
    )
    assert "+genpts" in missing
    assert "make_zero" not in missing
    negative, _plan = build_preset_command(
        Settings(),
        _capabilities(["libx264"], ["aac"]),
        "h264_main",
        tmp_path / "input.mp4",
        tmp_path / "output.partial.mp4",
        has_audio=True,
        media={"video": {"start_time_seconds": -0.04, "duration_seconds": 1.0}, "audio": {"codec": "aac"}},
    )
    assert "make_zero" in negative
    assert "+genpts" not in negative
    with pytest.raises(CommandError, match="Audio copy"):
        build_preset_command(
            Settings(),
            _capabilities(["libx264"], ["aac"]),
            "h264_main",
            tmp_path / "input.mp4",
            tmp_path / "output.partial.mp4",
            has_audio=True,
            request=ConvertRequest(preset="h264_main", audio_mode="copy"),
            media={"audio": {"codec": "opus"}},
        )
