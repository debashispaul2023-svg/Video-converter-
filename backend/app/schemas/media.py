"""Public media metadata. Raw ffprobe documents stay off this response model."""

from pydantic import BaseModel, Field


class VideoStreamInfo(BaseModel):
    codec: str | None = None
    codec_long_name: str | None = None
    profile: str | None = None
    width: int | None = None
    height: int | None = None
    pixel_format: str | None = None
    fps: float | None = None
    bitrate: int | None = None
    frame_count: int | None = None


class AudioStreamInfo(BaseModel):
    codec: str | None = None
    codec_long_name: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    channel_layout: str | None = None
    bitrate: int | None = None


class StreamCounts(BaseModel):
    video: int = 0
    audio: int = 0
    subtitle: int = 0
    other: int = 0


class MediaInfo(BaseModel):
    filename: str
    size_bytes: int
    duration_seconds: float | None = None
    container: str | None = None
    format_name: str | None = None
    format_long_name: str | None = None
    bitrate: int | None = None
    video: VideoStreamInfo | None = None
    audio: AudioStreamInfo | None = None
    stream_counts: StreamCounts = Field(default_factory=StreamCounts)
