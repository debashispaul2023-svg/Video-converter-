"""Structured conversion settings. Raw FFmpeg flags are rejected."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PresetId = Literal["h264_baseline", "h264_main", "h264_high", "h264_high10", "h265_hevc"]
QualityMode = Literal["crf", "bitrate", "target_size"]
QualityPreset = Literal["very_high", "high", "balanced", "smaller_file", "maximum_compression"]
CompressionPreset = Literal["small_file", "balanced", "high_quality", "maximum_compression"]
ResolutionChoice = Literal["original", "2160p", "1440p", "1080p", "720p", "480p", "360p", "custom"]
FpsChoice = Literal["original", "24", "25", "30", "50", "60", "custom"]
EncoderSpeed = Literal[
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
]
TuneChoice = Literal["none", "film", "animation", "grain", "stillimage", "fastdecode", "zerolatency"]
AudioMode = Literal["aac", "copy", "none"]
AudioBitrate = Literal["64k", "96k", "128k", "160k", "192k", "256k", "320k"]
VideoBitrate = Literal["500k", "1M", "2M", "4M", "6M", "8M", "10M", "15M", "20M"]
H264Level = Literal["auto", "3.0", "3.1", "3.2", "4.0", "4.1", "4.2", "5.0", "5.1", "5.2"]
ContainerId = Literal["mp4", "mov", "webm", "mkv", "avi", "mpegts"]
AudioCodecId = Literal["aac", "mp3", "opus", "vorbis", "flac", "alac", "ac3", "eac3", "mp2"]


class ConvertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: PresetId = "h264_main"
    format_id: str | None = None
    level: H264Level = "auto"
    container: ContainerId | None = None
    audio_codec: AudioCodecId | None = None
    quality_mode: QualityMode = "crf"
    crf: int | None = Field(default=None, ge=0, le=51)
    quality_preset: QualityPreset | None = None
    compression_preset: CompressionPreset | None = None
    video_bitrate: VideoBitrate | None = None
    target_size_bytes: int | None = Field(default=None, gt=0)
    resolution: ResolutionChoice = "original"
    width: int | None = Field(default=None, ge=2, le=7680)
    height: int | None = Field(default=None, ge=2, le=4320)
    fps: FpsChoice = "original"
    custom_fps: int | None = Field(default=None, ge=1, le=60)
    encoder_preset: EncoderSpeed | None = None
    tune: TuneChoice = "none"
    audio_mode: AudioMode = "aac"
    audio_bitrate: AudioBitrate = "128k"


class SizeEstimate(BaseModel):
    estimate: bool = True
    original_size_bytes: int
    estimated_output_size_bytes: int | None = None
    estimated_compression_percent: float | None = None
    note: str


class CompressionResult(BaseModel):
    original_size_bytes: int
    output_size_bytes: int
    size_reduction_bytes: int
    compression_percent: float
    larger_than_original: bool


class ConversionPreview(BaseModel):
    valid: bool
    preset: str
    encoder: str
    quality_mode: str
    crf: int | None = None
    video_bitrate_bps: int | None = None
    resolution: str
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    audio_mode: str
    audio_bitrate: str | None = None
    estimate: SizeEstimate
    errors: list[str] = Field(default_factory=list)
