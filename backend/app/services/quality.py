"""Validate structured quality settings and build safe filter values."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.schemas.conversion import ConvertRequest, SizeEstimate
from app.services.formats import level_fits

QUALITY_CRF = {
    "very_high": 18,
    "high": 21,
    "balanced": 23,
    "smaller_file": 27,
    "maximum_compression": 30,
}
COMPRESSION_CRF = {
    "small_file": 28,
    "balanced": 23,
    "high_quality": 20,
    "maximum_compression": 30,
}
RESOLUTION_HEIGHT = {
    "2160p": 2160,
    "1440p": 1440,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
    "360p": 360,
}
VIDEO_BITRATE_BPS = {
    "500k": 500_000,
    "1M": 1_000_000,
    "2M": 2_000_000,
    "4M": 4_000_000,
    "6M": 6_000_000,
    "8M": 8_000_000,
    "10M": 10_000_000,
    "15M": 15_000_000,
    "20M": 20_000_000,
}
AUDIO_BITRATE_BPS = {
    "64k": 64_000,
    "96k": 96_000,
    "128k": 128_000,
    "160k": 160_000,
    "192k": 192_000,
    "256k": 256_000,
    "320k": 320_000,
}
X265_TUNES = {"animation", "grain", "fastdecode", "zerolatency"}
MIN_VIDEO_BPS = 100_000


class PlanError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ConversionPlan:
    quality_mode: str
    crf: int | None
    video_bitrate_token: str | None
    video_bitrate_bps: int | None
    encoder_preset: str
    tune: str | None
    level: str
    audio_mode: str
    audio_bitrate: str | None
    width: int | None
    height: int | None
    fps: int | None
    scale_filter: str | None
    estimate: SizeEstimate


def build_plan(
    request: ConvertRequest,
    settings: Settings,
    preset: PresetDefinition,
    media: dict,
    *,
    source_has_audio: bool,
    original_size_bytes: int,
) -> ConversionPlan:
    if preset.encoder not in {"libx264", "libx265"}:
        raise PlanError("Quality controls are only available for the H.264 and H.265 presets.")
    crf = _crf(request, settings, preset)
    video_token, video_bps = _video_bitrate(request, settings, media, source_has_audio)
    speed = request.encoder_preset or (
        settings.default_h265_preset if preset.family == "hevc" else settings.default_x264_preset
    )
    tune = _tune(request.tune, preset.encoder)
    audio_mode, audio_bitrate = _audio(request, media, source_has_audio)
    width, height, scale = _resolution(request, media)
    fps = _fps(request, media)
    if request.level != "auto" and preset.family == "h264":
        if not level_fits(request.level, width, height, fps or (media.get("video") or {}).get("fps")):
            raise PlanError("This H.264 level does not support the requested resolution and frame rate.")
    if request.container and request.container != preset.container:
        raise PlanError("The selected container is not compatible with this preset.")
    if request.audio_codec and request.audio_codec != "aac" and preset.container == "mp4" and request.audio_codec not in {"mp3", "ac3", "eac3", "alac"}:
        raise PlanError("The selected audio codec is not compatible with this container.")
    estimate = _estimate(
        request.quality_mode,
        original_size_bytes,
        media,
        video_bps,
        audio_mode,
        audio_bitrate,
        request.target_size_bytes,
    )
    return ConversionPlan(
        quality_mode=request.quality_mode,
        crf=crf,
        video_bitrate_token=video_token,
        video_bitrate_bps=video_bps,
        encoder_preset=speed,
        tune=tune,
        level=request.level,
        audio_mode=audio_mode,
        audio_bitrate=audio_bitrate,
        width=width,
        height=height,
        fps=fps,
        scale_filter=scale,
        estimate=estimate,
    )


def compression_result(original_size_bytes: int, output_size_bytes: int) -> dict:
    reduction = original_size_bytes - output_size_bytes
    percent = 0.0
    if original_size_bytes > 0:
        percent = round((1 - output_size_bytes / original_size_bytes) * 100, 1)
    return {
        "original_size_bytes": original_size_bytes,
        "output_size_bytes": output_size_bytes,
        "size_reduction_bytes": reduction,
        "compression_percent": percent,
        "larger_than_original": output_size_bytes > original_size_bytes,
    }


def _crf(request: ConvertRequest, settings: Settings, preset: PresetDefinition) -> int | None:
    if request.quality_mode != "crf":
        return None
    selected = []
    if request.crf is not None:
        selected.append(request.crf)
    if request.quality_preset is not None:
        selected.append(QUALITY_CRF[request.quality_preset])
    if request.compression_preset is not None:
        selected.append(COMPRESSION_CRF[request.compression_preset])
    if len(selected) > 1 and len(set(selected)) > 1:
        raise PlanError("Choose only one of CRF, quality preset, or compression preset.")
    if selected:
        return selected[0]
    return settings.default_h265_crf if preset.family == "hevc" else settings.default_h264_crf


def _video_bitrate(request: ConvertRequest, settings: Settings, media: dict, source_has_audio: bool) -> tuple[str | None, int | None]:
    if request.quality_mode == "crf":
        if request.video_bitrate is not None or request.target_size_bytes is not None:
            raise PlanError("Video bitrate and target size are only used in their matching quality mode.")
        return None, None
    if request.quality_mode == "bitrate":
        if request.video_bitrate is None:
            raise PlanError("A video bitrate preset is required for bitrate mode.")
        return request.video_bitrate, VIDEO_BITRATE_BPS[request.video_bitrate]
    if request.target_size_bytes is None:
        raise PlanError("A target size is required for target-size mode.")
    if request.target_size_bytes > settings.max_target_size_bytes:
        raise PlanError("The requested target size is too large.")
    duration = _duration(media)
    if duration is None or duration <= 0:
        raise PlanError("Target size cannot be calculated because the video duration is unknown.")
    audio_bps = AUDIO_BITRATE_BPS[request.audio_bitrate] if source_has_audio and request.audio_mode == "aac" else 0
    audio_bytes = int(audio_bps * duration / 8)
    overhead = max(65_536, int(request.target_size_bytes * 0.02))
    available = request.target_size_bytes - audio_bytes - overhead
    if available <= 0:
        raise PlanError("The requested target size is too small for this video and selected settings.")
    video_bps = int(available * 8 / duration)
    if video_bps < MIN_VIDEO_BPS:
        raise PlanError("The requested target size is too small for this video and selected settings.")
    return f"{max(1, video_bps // 1000)}k", video_bps


def _audio(request: ConvertRequest, media: dict, source_has_audio: bool) -> tuple[str, str | None]:
    if request.audio_mode == "none" or not source_has_audio and request.audio_mode == "aac":
        if request.audio_mode != "none" and not source_has_audio:
            return "none", None
        return "none", None
    if not source_has_audio:
        raise PlanError("This video has no audio stream to copy.")
    if request.audio_mode == "copy":
        codec = ((media.get("audio") or {}).get("codec") or "").lower()
        if codec != "aac":
            raise PlanError("Audio copy is only available when the source audio is AAC in an MP4 output.")
        return "copy", None
    return "aac", request.audio_bitrate


def _resolution(request: ConvertRequest, media: dict) -> tuple[int | None, int | None, str | None]:
    video = media.get("video") or {}
    source_width = video.get("width")
    source_height = video.get("height")
    if request.resolution == "original":
        if request.width or request.height:
            raise PlanError("Custom dimensions require the custom resolution setting.")
        return source_width, source_height, None
    if request.resolution == "custom":
        if not request.width or not request.height:
            raise PlanError("Custom resolution requires width and height.")
        width, height = _even(request.width), _even(request.height)
        scale = f"scale={width}:{height}:force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2"
    else:
        height = RESOLUTION_HEIGHT[request.resolution]
        if not source_width or not source_height:
            raise PlanError("Resolution cannot be changed because the source dimensions are unknown.")
        width = _even(int(source_width * height / source_height))
        height = _even(height)
        scale = f"scale=-2:{height}"
    if source_width and source_height and (width > source_width or height > source_height):
        raise PlanError("This resolution would upscale the video. Upscaling is not enabled.")
    if width < 2 or height < 2:
        raise PlanError("The requested resolution is too small.")
    return width, height, scale


def _fps(request: ConvertRequest, media: dict) -> int | None:
    if request.fps == "original":
        if request.custom_fps is not None:
            raise PlanError("Custom FPS requires the custom frame-rate setting.")
        return None
    requested = request.custom_fps if request.fps == "custom" else int(request.fps)
    if request.fps == "custom" and requested is None:
        raise PlanError("Custom FPS requires a frame-rate value.")
    source = (media.get("video") or {}).get("fps")
    if source is not None and requested > source + 0.01:
        raise PlanError("This frame rate is higher than the source. Increasing FPS is not enabled.")
    return requested


def _tune(tune: str, encoder: str) -> str | None:
    if tune == "none":
        return None
    if encoder == "libx264":
        return tune
    if encoder == "libx265" and tune in X265_TUNES:
        return tune
    raise PlanError("The selected tune is not supported by this encoder.")


def _estimate(
    mode: str,
    original_size_bytes: int,
    media: dict,
    video_bps: int | None,
    audio_mode: str,
    audio_bitrate: str | None,
    target_size_bytes: int | None,
) -> SizeEstimate:
    duration = _duration(media)
    estimated = None
    if mode == "target_size" and target_size_bytes:
        estimated = target_size_bytes
        note = "Target-size estimate. FFmpeg may not match this size exactly."
    elif mode == "bitrate" and video_bps and duration:
        audio_bps = AUDIO_BITRATE_BPS.get(audio_bitrate or "", 0) if audio_mode == "aac" else 0
        estimated = int((video_bps + audio_bps) * duration / 8 * 1.03)
        note = "Bitrate estimate. Container overhead can change the final size."
    else:
        note = "CRF estimate is approximate. Lower CRF usually means higher quality and a larger file."
    percent = None
    if estimated is not None and original_size_bytes > 0:
        percent = round((1 - estimated / original_size_bytes) * 100, 1)
    return SizeEstimate(
        estimate=True,
        original_size_bytes=original_size_bytes,
        estimated_output_size_bytes=estimated,
        estimated_compression_percent=percent,
        note=note,
    )


def _duration(media: dict) -> float | None:
    value = media.get("duration_seconds")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _even(value: int) -> int:
    return value if value % 2 == 0 else value - 1
