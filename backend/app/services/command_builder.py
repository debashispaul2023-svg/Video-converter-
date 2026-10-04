"""Build FFmpeg argument lists from trusted presets. User text is never a command."""

from __future__ import annotations

import re
from pathlib import Path

from app.config import Settings
from app.schemas.capabilities import CapabilitiesResponse
from app.schemas.conversion import ConvertRequest
from app.services.presets import PresetDefinition, require_preset
from app.services.formats import container_allows
from app.services.quality import ConversionPlan, PlanError, build_plan

_AAC_BITRATE_RE = re.compile(r"^(?:64|96|128|160|192|256|320)k$")
_BITRATE_TOKEN_RE = re.compile(r"^(?:500k|1M|2M|4M|6M|8M|10M|15M|20M|[1-9][0-9]{1,5}k)$")
H264_MAIN_PRESET = "h264_main"


class CommandError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class EncoderUnavailable(CommandError):
    pass


def build_h264_main_command(
    settings: Settings,
    capabilities: CapabilitiesResponse,
    input_path: Path,
    output_path: Path,
    *,
    has_audio: bool,
) -> list[str]:
    """Keep the Phase 3 entry point. It delegates to the shared builder."""

    return build_preset_command(
        settings,
        capabilities,
        H264_MAIN_PRESET,
        input_path,
        output_path,
        has_audio=has_audio,
    )[0]


def build_preset_command(
    settings: Settings,
    capabilities: CapabilitiesResponse,
    preset_id: str,
    input_path: Path,
    output_path: Path,
    *,
    has_audio: bool,
    high10_supported: bool | None = None,
    request: ConvertRequest | None = None,
    media: dict | None = None,
    original_size_bytes: int = 0,
) -> tuple[list[str], ConversionPlan]:
    try:
        preset = require_preset(
            preset_id,
            capabilities,
            high10_supported=high10_supported,
            ffmpeg_binary=settings.ffmpeg_binary,
        )
    except KeyError:
        raise CommandError("This preset is not available.") from None
    except LookupError as exc:
        raise EncoderUnavailable(str(exc)) from None
    plan_request = request or ConvertRequest(preset=preset_id)
    if plan_request.audio_mode == "aac" and has_audio and "aac" not in capabilities.summary.aac.encoders:
        raise EncoderUnavailable(
            f"{preset.label} conversion is unavailable because this FFmpeg build does not provide AAC."
        )
    try:
        plan = build_plan(
            plan_request,
            settings,
            preset,
            media or {},
            source_has_audio=has_audio,
            original_size_bytes=original_size_bytes,
        )
    except PlanError as exc:
        raise CommandError(exc.message) from None
    _reject_incompatible_audio_copy(plan, media or {}, preset.container, preset.family)
    if input_path.suffix == "" or output_path.name not in {"output.mp4", "output.partial.mp4"}:
        raise CommandError("Output path is not a server-generated MP4.")
    return _arguments(preset, plan, input_path, output_path, media or {}), plan


def _arguments(
    preset: PresetDefinition,
    plan: ConversionPlan,
    input_path: Path,
    output_path: Path,
    media: dict,
) -> list[str]:
    command = ["-hide_banner", "-nostdin", "-y"]
    if _missing_timestamps(media):
        command.extend(["-fflags", "+genpts"])
    command.extend(["-i", str(input_path), "-map", "0:v:0"])
    if _negative_timestamps(media):
        command.extend(["-avoid_negative_ts", "make_zero"])
    command.extend([
        "-c:v",
        preset.encoder,
        "-profile:v",
        preset.profile,
        "-pix_fmt",
        preset.pixel_format,
        "-preset",
        plan.encoder_preset,
    ])
    if plan.quality_mode == "crf":
        command.extend(["-crf", str(plan.crf)])
    else:
        token = plan.video_bitrate_token or ""
        if not _BITRATE_TOKEN_RE.fullmatch(token):
            raise CommandError("The video bitrate is not valid.")
        command.extend(["-b:v", token])
    if plan.tune:
        command.extend(["-tune", plan.tune])
    if plan.level and plan.level != "auto" and preset.family == "h264":
        command.extend(["-level:v", plan.level])
    filters = [item for item in (plan.scale_filter, f"fps={plan.fps}" if plan.fps else None) if item]
    if filters:
        command.extend(["-vf", ",".join(filters)])
    if plan.audio_mode == "aac":
        if not plan.audio_bitrate or not _AAC_BITRATE_RE.fullmatch(plan.audio_bitrate):
            raise CommandError("The audio bitrate is not valid.")
        command.extend(["-map", "0:a:0", "-c:a", "aac", "-ac", "2", "-b:a", plan.audio_bitrate])
        if plan.fps:
            command.extend(["-af", "aresample=async=1:first_pts=0"])
    elif plan.audio_mode == "copy":
        command.extend(["-map", "0:a:0", "-c:a", "copy"])
    else:
        command.append("-an")
    command.extend(["-movflags", "+faststart", str(output_path)])
    return command


def _missing_timestamps(media: dict) -> bool:
    streams = [media.get("video") or {}, media.get("audio") or {}]
    return any(stream.get("timestamps_missing") is True for stream in streams)


def _negative_timestamps(media: dict) -> bool:
    for stream in (media.get("video") or {}, media.get("audio") or {}):
        start = stream.get("start_time_seconds")
        if isinstance(start, (int, float)) and start < 0:
            return True
    return False


def _reject_incompatible_audio_copy(plan: ConversionPlan, media: dict, container: str, family: str) -> None:
    if plan.audio_mode != "copy":
        return
    codec = ((media.get("audio") or {}).get("codec") or "").lower()
    if not codec or not container_allows(container, family, codec):
        raise CommandError("Copying this audio is not compatible with the output container.")
