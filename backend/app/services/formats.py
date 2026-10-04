"""Explicit codec, profile, level, and container rules. Discovery is not a preset."""

from __future__ import annotations

from dataclasses import dataclass

H264_LEVELS = {
    "3.0": (720, 576, 30),
    "3.1": (1280, 720, 30),
    "3.2": (1280, 720, 60),
    "4.0": (1920, 1080, 30),
    "4.1": (1920, 1080, 30),
    "4.2": (1920, 1080, 60),
    "5.0": (2560, 1600, 30),
    "5.1": (3840, 2160, 30),
    "5.2": (3840, 2160, 60),
}

CONTAINER_VIDEO = {
    "mp4": {"h264", "hevc", "mpeg4", "av1"},
    "mov": {"h264", "hevc", "prores"},
    "webm": {"vp8", "vp9", "av1"},
    "mkv": {"h264", "hevc", "vp8", "vp9", "av1", "mpeg4", "mpeg2video", "ffv1"},
    "avi": {"mpeg4"},
    "mpegts": {"mpeg2video", "mpeg1video", "h264"},
}
CONTAINER_AUDIO = {
    "mp4": {"aac", "mp3", "ac3", "eac3", "alac"},
    "mov": {"aac", "alac", "ac3"},
    "webm": {"opus", "vorbis"},
    "mkv": {"aac", "mp3", "opus", "vorbis", "flac", "ac3", "eac3"},
    "avi": {"mp3"},
    "mpegts": {"aac", "mp2", "ac3"},
}


@dataclass(frozen=True)
class FormatDefinition:
    format_id: str
    label: str
    encoder: str
    family: str
    container: str
    profile: str | None
    pixel_format: str
    expected_codec: str
    audio_encoder: str
    audio_codec: str


FORMATS = {
    "av1_webm": FormatDefinition("av1_webm", "AV1 WebM", "libaom-av1", "av1", "webm", None, "yuv420p", "av1", "libopus", "opus"),
    "vp9_webm": FormatDefinition("vp9_webm", "VP9 WebM", "libvpx-vp9", "vp9", "webm", None, "yuv420p", "vp9", "libopus", "opus"),
    "vp8_webm": FormatDefinition("vp8_webm", "VP8 WebM", "libvpx", "vp8", "webm", None, "yuv420p", "vp8", "libvorbis", "vorbis"),
    "mpeg4_mp4": FormatDefinition("mpeg4_mp4", "MPEG-4 MP4", "mpeg4", "mpeg4", "mp4", None, "yuv420p", "mpeg4", "aac", "aac"),
    "mpeg2_ts": FormatDefinition("mpeg2_ts", "MPEG-2 TS", "mpeg2video", "mpeg2video", "mpegts", None, "yuv420p", "mpeg2video", "mp2", "mp2"),
}


def level_fits(level: str, width: int | None, height: int | None, fps: float | None) -> bool:
    if level == "auto":
        return True
    limit = H264_LEVELS.get(level)
    if limit is None:
        return False
    max_width, max_height, max_fps = limit
    if width and height and (width > max_width or height > max_height):
        return False
    if fps and fps > max_fps + 0.01:
        return False
    return True


def format_catalog(encoder_names: set[str], audio_names: set[str]) -> list[dict]:
    catalog = []
    for item in FORMATS.values():
        available = item.encoder in encoder_names and item.audio_encoder in audio_names
        catalog.append({
            "id": item.format_id,
            "name": item.label,
            "available": available,
            "encoder": item.encoder if available else None,
            "container": item.container,
            "video_codec": item.family,
            "audio_codec": item.audio_codec,
            "note": "Available." if available else "Encoder or audio codec is not installed.",
        })
    return catalog


def container_allows(container: str, family: str, audio_codec: str | None) -> bool:
    if family not in CONTAINER_VIDEO.get(container, set()):
        return False
    if audio_codec and audio_codec not in CONTAINER_AUDIO.get(container, set()):
        return False
    return True
