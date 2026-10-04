"""Run ffprobe with a fixed argument list and parse media metadata."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import Any

from app.config import Settings
from app.schemas.media import AudioStreamInfo, MediaInfo, StreamCounts, VideoStreamInfo

logger = logging.getLogger(__name__)


class ProbeError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class ProbeUnavailable(ProbeError):
    def __init__(self) -> None:
        super().__init__("Media analysis service is unavailable.")


class ProbeTimeout(ProbeError):
    def __init__(self) -> None:
        super().__init__("Media analysis timed out.")


class InvalidMedia(ProbeError):
    def __init__(self, message: str = "Uploaded file is not a valid media file.") -> None:
        super().__init__(message)


class FFProbeService:
    def __init__(self, settings: Settings) -> None:
        self.binary = settings.ffprobe_binary
        self.timeout = settings.ffprobe_timeout_seconds

    def analyze(self, media_path: str, display_name: str, size_bytes: int) -> tuple[MediaInfo, dict]:
        binary = self._resolve_binary()
        if binary is None:
            logger.error("ffprobe unavailable")
            raise ProbeUnavailable()
        logger.info("ffprobe start")
        document = self._run(binary, media_path)
        logger.info("ffprobe complete")
        media = parse_probe_document(document, filename=display_name, size_bytes=size_bytes)
        if media.video is None:
            raise InvalidMedia("Uploaded file is not a valid media file.")
        return media, document

    def _resolve_binary(self) -> str | None:
        candidate = self.binary.strip()
        if not candidate or any(char in candidate for char in ("\n", "\r", "\x00")):
            return None
        if "/" in candidate:
            return candidate if shutil.which(candidate) else None
        return shutil.which(candidate)

    def _run(self, binary: str, media_path: str) -> dict:
        command = [
            binary,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-show_programs",
            media_path,
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.error("ffprobe timeout")
            raise ProbeTimeout() from None
        except OSError:
            logger.exception("ffprobe failed to start")
            raise ProbeUnavailable() from None
        if completed.returncode != 0 or not completed.stdout.strip():
            logger.error("ffprobe rejected media")
            raise InvalidMedia("Unable to analyze this media file.")
        try:
            document = json.loads(completed.stdout)
        except json.JSONDecodeError:
            logger.error("ffprobe returned malformed json")
            raise InvalidMedia("Unable to analyze this media file.") from None
        if not isinstance(document, dict):
            raise InvalidMedia("Unable to analyze this media file.")
        return document


def parse_probe_document(document: dict, *, filename: str, size_bytes: int) -> MediaInfo:
    """Parse an ffprobe JSON document. Missing optional fields become null."""

    fmt = document.get("format") if isinstance(document.get("format"), dict) else {}
    streams = document.get("streams") if isinstance(document.get("streams"), list) else []
    video_streams = [stream for stream in streams if _stream_type(stream) == "video" and not _attached_pic(stream)]
    if not video_streams:
        video_streams = [stream for stream in streams if _stream_type(stream) == "video"]
    audio_streams = [stream for stream in streams if _stream_type(stream) == "audio"]
    subtitle_count = sum(1 for stream in streams if _stream_type(stream) == "subtitle")
    other_count = sum(
        1 for stream in streams if _stream_type(stream) not in {"video", "audio", "subtitle"}
    )
    video = _video(video_streams[0]) if video_streams else None
    audio = _audio(audio_streams[0]) if audio_streams else None
    return MediaInfo(
        filename=filename,
        size_bytes=size_bytes,
        duration_seconds=_duration(fmt, video_streams[0] if video_streams else None),
        container=fmt.get("format_name"),
        format_name=fmt.get("format_name"),
        format_long_name=fmt.get("format_long_name"),
        bitrate=_int_or_none(fmt.get("bit_rate")),
        video=video,
        audio=audio,
        stream_counts=StreamCounts(
            video=len(video_streams),
            audio=len(audio_streams),
            subtitle=subtitle_count,
            other=other_count,
        ),
    )


def parse_rate(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"0/0", "N/A"}:
        return None
    try:
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            denominator_value = float(denominator)
            if denominator_value == 0:
                return None
            return float(numerator) / denominator_value
        return float(text)
    except (TypeError, ValueError):
        return None


def _video(stream: dict) -> VideoStreamInfo:
    fps = parse_rate(stream.get("avg_frame_rate"))
    if fps is None:
        fps = parse_rate(stream.get("r_frame_rate"))
    return VideoStreamInfo(
        codec=stream.get("codec_name"),
        codec_long_name=stream.get("codec_long_name"),
        profile=stream.get("profile"),
        width=_int_or_none(stream.get("width")),
        height=_int_or_none(stream.get("height")),
        pixel_format=stream.get("pix_fmt"),
        fps=fps,
        bitrate=_int_or_none(stream.get("bit_rate")),
        frame_count=_frame_count(stream),
    )


def _audio(stream: dict) -> AudioStreamInfo:
    layout = stream.get("channel_layout")
    return AudioStreamInfo(
        codec=stream.get("codec_name"),
        codec_long_name=stream.get("codec_long_name"),
        sample_rate=_int_or_none(stream.get("sample_rate")),
        channels=_int_or_none(stream.get("channels")),
        channel_layout=layout,
        bitrate=_int_or_none(stream.get("bit_rate")),
    )


def _duration(fmt: dict, video_stream: dict | None) -> float | None:
    duration = parse_rate(fmt.get("duration"))
    if duration is not None:
        return duration
    if video_stream is None:
        return None
    return parse_rate(video_stream.get("duration"))


def _frame_count(stream: dict) -> int | None:
    for key in ("nb_frames", "nb_read_frames"):
        parsed = _int_or_none(stream.get(key))
        if parsed is not None:
            return parsed
    return None


def _stream_type(stream: Any) -> str:
    if not isinstance(stream, dict):
        return ""
    return str(stream.get("codec_type") or "")


def _attached_pic(stream: dict) -> bool:
    disposition = stream.get("disposition")
    return isinstance(disposition, dict) and disposition.get("attached_pic") == 1


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "N/A":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
