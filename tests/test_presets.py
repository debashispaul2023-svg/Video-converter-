"""Preset availability fixtures. These do not depend on the installed FFmpeg."""

from app.schemas.capabilities import (
    BinaryStatus,
    CapabilitiesResponse,
    CapabilitySummary,
    CodecAvailability,
    HardwareAcceleration,
    PixelFormatInfo,
    StreamTool,
)
from app.services.command_builder import EncoderUnavailable, build_preset_command
from app.services.presets import preset_catalog
from app.config import Settings
from pathlib import Path
import pytest


def _codec(names: list[str]) -> CodecAvailability:
    return CodecAvailability(
        available=bool(names),
        encoders=names,
        preferred_encoder=names[0] if names else None,
        note="fixture",
    )


def _capabilities(h264: list[str], hevc: list[str], *, high10_pixel: bool = True) -> CapabilitiesResponse:
    hardware = HardwareAcceleration(available=False, methods=[], hardware_video_encoders=[], note="cpu")
    pixels = []
    if high10_pixel:
        pixels.append(
            PixelFormatInfo(name="yuv420p10le", input_supported=True, output_supported=True)
        )
    video = [
        StreamTool(name=name, description=name, media_type="video", codec="hevc", hardware=True)
        for name in hevc
        if name != "libx265"
    ]
    return CapabilitiesResponse(
        summary=CapabilitySummary(
            ffmpeg_available=True,
            ffprobe_available=True,
            video_encoder_count=len(h264) + len(hevc),
            audio_encoder_count=1,
            subtitle_encoder_count=0,
            video_decoder_count=0,
            audio_decoder_count=0,
            muxer_count=1,
            demuxer_count=1,
            pixel_format_count=len(pixels),
            h264=_codec(h264),
            hevc=_codec(hevc),
            aac=_codec(["aac"]),
            hardware_acceleration=hardware,
        ),
        ffmpeg=BinaryStatus(name="ffmpeg", available=True, version="test"),
        ffprobe=BinaryStatus(name="ffprobe", available=True, version="test"),
        video_encoders=video,
        audio_encoders=[],
        subtitle_encoders=[],
        video_decoders=[],
        audio_decoders=[],
        subtitle_decoders=[],
        muxers=[],
        demuxers=[],
        pixel_formats=pixels,
        hardware_acceleration=hardware,
    )


def test_catalog_scenarios() -> None:
    both = preset_catalog(_capabilities(["libx264"], ["libx265"]), high10_supported=True)
    assert both["h264_baseline"].available is True
    assert both["h264_main"].available is True
    assert both["h264_high"].available is True
    assert both["h264_high10"].available is True
    assert both["h265_hevc"].available is True
    assert both["h265_hevc"].encoder == "libx265"

    h264_only = preset_catalog(_capabilities(["libx264"], []), high10_supported=True)
    assert h264_only["h264_main"].available is True
    assert h264_only["h265_hevc"].available is False

    hevc_only = preset_catalog(_capabilities([], ["libx265"]), high10_supported=False)
    assert hevc_only["h264_baseline"].available is False
    assert hevc_only["h264_main"].available is False
    assert hevc_only["h265_hevc"].available is True

    no_high10 = preset_catalog(_capabilities(["libx264"], ["libx265"]), high10_supported=False)
    assert no_high10["h264_high10"].available is False
    assert no_high10["h264_high"].available is True


def test_hardware_hevc_is_not_a_selectable_preset() -> None:
    catalog = preset_catalog(
        _capabilities(["libx264"], ["libx265", "hevc_v4l2m2m"]),
        high10_supported=False,
    )
    assert catalog["h265_hevc"].encoder == "libx265"
    assert "hevc_v4l2m2m" in catalog["h265_hevc"].detected_hardware_encoders
    assert catalog["h265_hevc"].available is True


def test_commands_use_registry_encoders(tmp_path: Path) -> None:
    settings = Settings()
    capabilities = _capabilities(["libx264"], ["libx265"])
    source = tmp_path / "input.mp4"
    output = tmp_path / "output.mp4"
    baseline = build_preset_command(
        settings, capabilities, "h264_baseline", source, output, has_audio=True, high10_supported=True
    )[0]
    high = build_preset_command(
        settings, capabilities, "h264_high", source, output, has_audio=True, high10_supported=True
    )[0]
    high10 = build_preset_command(
        settings, capabilities, "h264_high10", source, output, has_audio=False, high10_supported=True
    )[0]
    hevc = build_preset_command(
        settings, capabilities, "h265_hevc", source, output, has_audio=True, high10_supported=True
    )[0]
    assert baseline[baseline.index("-c:v") + 1] == "libx264"
    assert baseline[baseline.index("-profile:v") + 1] == "baseline"
    assert baseline[baseline.index("-pix_fmt") + 1] == "yuv420p"
    assert high[high.index("-profile:v") + 1] == "high"
    assert high10[high10.index("-pix_fmt") + 1] == "yuv420p10le"
    assert "-an" in high10
    assert hevc[hevc.index("-c:v") + 1] == "libx265"
    assert hevc[hevc.index("-pix_fmt") + 1] == "yuv420p"
    with pytest.raises(EncoderUnavailable, match="libx265"):
        build_preset_command(
            settings,
            _capabilities(["libx264"], []),
            "h265_hevc",
            source,
            output,
            has_audio=True,
            high10_supported=False,
        )
