"""Structured quality validation. These tests do not start FFmpeg."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.schemas.conversion import ConvertRequest
from app.services.command_builder import CommandError, build_preset_command
from app.services.presets import PRESETS
from app.services.quality import PlanError, build_plan
from tests.test_presets import _capabilities

MEDIA = {
    "duration_seconds": 10.0,
    "size_bytes": 5_000_000,
    "video": {"width": 1280, "height": 720, "fps": 30.0, "codec": "h264"},
    "audio": {"codec": "aac", "channels": 2},
}


def _plan(request: ConvertRequest, media: dict | None = None, has_audio: bool = True):
    return build_plan(
        request,
        Settings(),
        PRESETS[request.preset],
        media or MEDIA,
        source_has_audio=has_audio,
        original_size_bytes=5_000_000,
    )


def test_crf_presets_and_ranges() -> None:
    assert _plan(ConvertRequest(quality_preset="very_high")).crf == 18
    assert _plan(ConvertRequest(quality_preset="balanced")).crf == 23
    assert _plan(ConvertRequest(compression_preset="maximum_compression")).crf == 30
    assert _plan(ConvertRequest(preset="h265_hevc")).crf == 28
    with pytest.raises(ValidationError):
        ConvertRequest(crf=80)
    with pytest.raises(PlanError):
        _plan(ConvertRequest(crf=18, quality_preset="maximum_compression"))


def test_bitrate_and_target_size() -> None:
    bitrate = _plan(ConvertRequest(quality_mode="bitrate", video_bitrate="2M"))
    assert bitrate.video_bitrate_bps == 2_000_000
    target = _plan(ConvertRequest(quality_mode="target_size", target_size_bytes=2_000_000))
    assert target.video_bitrate_bps is not None
    assert target.video_bitrate_bps >= 100_000
    assert target.estimate.estimate is True
    with pytest.raises(PlanError, match="too small"):
        _plan(ConvertRequest(quality_mode="target_size", target_size_bytes=50_000))
    with pytest.raises(ValidationError):
        ConvertRequest(quality_mode="bitrate", video_bitrate="12k")


def test_resolution_fps_and_audio_are_bounded() -> None:
    downscale = _plan(ConvertRequest(resolution="480p"))
    assert downscale.height == 480
    assert downscale.width == 852 or downscale.width == 854
    assert downscale.scale_filter is not None
    assert "scale=" in downscale.scale_filter
    assert "-vf" not in downscale.scale_filter
    with pytest.raises(PlanError, match="upscale"):
        _plan(ConvertRequest(resolution="1080p"))
    with pytest.raises(ValidationError):
        ConvertRequest(resolution="custom", width=1, height=1)
    assert _plan(ConvertRequest(fps="30")).fps == 30
    with pytest.raises(PlanError, match="higher than the source"):
        _plan(ConvertRequest(fps="60"))
    with pytest.raises(ValidationError):
        ConvertRequest(fps="custom", custom_fps=999999)
    silent = _plan(ConvertRequest(audio_mode="none"), has_audio=True)
    assert silent.audio_mode == "none"
    copied = _plan(ConvertRequest(audio_mode="copy"))
    assert copied.audio_mode == "copy"


def test_command_uses_validated_values_and_rejects_injection(tmp_path: Path) -> None:
    command, plan = build_preset_command(
        Settings(),
        _capabilities(["libx264"], ["libx265"]),
        "h264_main",
        tmp_path / "input.mp4",
        tmp_path / "output.mp4",
        has_audio=True,
        request=ConvertRequest(quality_mode="bitrate", video_bitrate="1M", resolution="720p", audio_bitrate="128k"),
        media=MEDIA,
        original_size_bytes=5_000_000,
    )
    assert command[command.index("-b:v") + 1] == "1M"
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-b:a") + 1] == "128k"
    assert plan.estimate.estimate is True
    assert "-vf" in command
    assert all(";" not in part and "&&" not in part for part in command)
    with pytest.raises(ValidationError):
        ConvertRequest.model_validate({"preset": "h264_main", "vf": "scale=iw:ih,evil"})
    with pytest.raises(CommandError, match="tune"):
        build_preset_command(
            Settings(),
            _capabilities(["libx264"], ["libx265"]),
            "h265_hevc",
            tmp_path / "input.mp4",
            tmp_path / "output.mp4",
            has_audio=False,
            request=ConvertRequest(preset="h265_hevc", tune="film", audio_mode="none"),
            high10_supported=False,
        )
