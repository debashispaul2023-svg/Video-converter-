"""Central preset registry. Availability comes from detected encoders, not names we hope exist."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from app.config import Settings
from app.schemas.capabilities import CapabilitiesResponse, PresetAvailability

_HIGH10_PIXEL = "yuv420p10le"
_X264_PRESETS = {
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
}


@dataclass(frozen=True)
class PresetDefinition:
    preset_id: str
    encoder: str
    family: str
    container: str
    profile: str
    pixel_format: str
    expected_codec: str
    accepted_profiles: tuple[str, ...]
    label: str


PRESETS: dict[str, PresetDefinition] = {
    "h264_baseline": PresetDefinition(
        "h264_baseline",
        "libx264",
        "h264",
        "mp4",
        "baseline",
        "yuv420p",
        "h264",
        ("baseline", "constrained baseline"),
        "H.264 Baseline",
    ),
    "h264_main": PresetDefinition(
        "h264_main",
        "libx264",
        "h264",
        "mp4",
        "main",
        "yuv420p",
        "h264",
        ("main",),
        "H.264 Main",
    ),
    "h264_high": PresetDefinition(
        "h264_high",
        "libx264",
        "h264",
        "mp4",
        "high",
        "yuv420p",
        "h264",
        ("high",),
        "H.264 High",
    ),
    "h264_high10": PresetDefinition(
        "h264_high10",
        "libx264",
        "h264",
        "mp4",
        "high10",
        _HIGH10_PIXEL,
        "h264",
        ("high 10", "high10"),
        "H.264 High 10",
    ),
    "h265_hevc": PresetDefinition(
        "h265_hevc",
        "libx265",
        "hevc",
        "mp4",
        "main",
        "yuv420p",
        "hevc",
        ("main", "main 10"),
        "H.265/HEVC",
    ),
}


def preset_catalog(
    capabilities: CapabilitiesResponse,
    *,
    high10_supported: bool | None = None,
    ffmpeg_binary: str = "ffmpeg",
) -> dict[str, PresetAvailability]:
    """Return every known preset. Unavailable presets stay visible but not selectable."""

    high10 = (
        _libx264_supports_high10(ffmpeg_binary)
        if high10_supported is None
        else high10_supported
    )
    hardware_hevc = [
        tool.name
        for tool in capabilities.video_encoders
        if tool.codec == "hevc" and tool.hardware and tool.name != "libx265"
    ]
    catalog: dict[str, PresetAvailability] = {}
    for preset_id, preset in PRESETS.items():
        encoder_ready = preset.encoder in _encoders_for(capabilities, preset.family)
        if preset_id == "h264_high10":
            encoder_ready = encoder_ready and high10 and _pixel_listed(capabilities, _HIGH10_PIXEL)
        note = _note(preset, encoder_ready, hardware_hevc)
        catalog[preset_id] = PresetAvailability(
            available=encoder_ready,
            encoder=preset.encoder if encoder_ready else None,
            container=preset.container if encoder_ready else None,
            profile=preset.profile if encoder_ready else None,
            pixel_format=preset.pixel_format if encoder_ready else None,
            note=note,
            detected_hardware_encoders=hardware_hevc if preset_id == "h265_hevc" else [],
        )
    return catalog


def require_preset(
    preset_id: str,
    capabilities: CapabilitiesResponse,
    *,
    high10_supported: bool | None = None,
    ffmpeg_binary: str = "ffmpeg",
) -> PresetDefinition:
    preset = PRESETS.get(preset_id)
    if preset is None:
        raise KeyError(preset_id)
    status = preset_catalog(
        capabilities,
        high10_supported=high10_supported,
        ffmpeg_binary=ffmpeg_binary,
    )[preset_id]
    if not status.available:
        if preset.family == "hevc":
            raise LookupError(
                "H.265/HEVC is unavailable because this FFmpeg build does not provide libx265."
            )
        if preset_id == "h264_high10":
            raise LookupError(
                "H.264 High 10 is unavailable because this FFmpeg build does not support it."
            )
        raise LookupError(
            f"{preset.label} conversion is unavailable because this FFmpeg build does not provide {preset.encoder}."
        )
    return preset


def quality_args(settings: Settings, preset: PresetDefinition) -> tuple[str, str]:
    if preset.family == "hevc":
        speed = settings.default_h265_preset
        crf = settings.default_h265_crf
    else:
        speed = settings.default_x264_preset
        crf = settings.default_h264_crf
    if speed not in _X264_PRESETS:
        raise ValueError("The configured encoder preset is not valid.")
    if not 0 <= crf <= 51:
        raise ValueError("The configured CRF is not valid.")
    return speed, str(crf)


def _encoders_for(capabilities: CapabilitiesResponse, family: str) -> list[str]:
    if family == "h264":
        return capabilities.summary.h264.encoders
    if family == "hevc":
        return capabilities.summary.hevc.encoders
    return []


def _pixel_listed(capabilities: CapabilitiesResponse, name: str) -> bool:
    if not capabilities.pixel_formats:
        return True
    return any(item.name == name and item.output_supported for item in capabilities.pixel_formats)


def _note(preset: PresetDefinition, available: bool, hardware_hevc: list[str]) -> str:
    if preset.preset_id == "h265_hevc" and hardware_hevc:
        detected = ", ".join(hardware_hevc)
        if available:
            return f"libx265 is available. Detected hardware HEVC encoders are not selectable: {detected}."
        return f"libx265 is unavailable. Detected hardware HEVC encoders are not selectable: {detected}."
    if available:
        return f"{preset.label} is available with {preset.encoder}."
    if preset.preset_id == "h264_high10":
        return "H.264 High 10 is not supported by the installed libx264 build."
    return f"{preset.label} is unavailable because {preset.encoder} was not detected."


def _libx264_supports_high10(ffmpeg_binary: str) -> bool:
    binary = shutil.which(ffmpeg_binary) if "/" not in ffmpeg_binary else ffmpeg_binary
    if not binary:
        return False
    try:
        completed = subprocess.run(
            [binary, "-hide_banner", "-h", "encoder=libx264"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    text = completed.stdout or ""
    for line in text.splitlines():
        if line.strip().lower().startswith("supported pixel formats:"):
            return _HIGH10_PIXEL in line.split()
    return False
