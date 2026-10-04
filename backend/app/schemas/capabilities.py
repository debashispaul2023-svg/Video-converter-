"""Pydantic models for capability detection. Availability is never invented."""

from typing import Literal

from pydantic import BaseModel, Field


class BinaryStatus(BaseModel):
    name: str
    available: bool
    version: str | None = None
    build_configuration: str | None = None
    error: str | None = None


class StreamTool(BaseModel):
    """A detected encoder or decoder. Only tools FFmpeg listed are included."""

    name: str
    description: str
    media_type: Literal["video", "audio", "subtitle"]
    codec: str | None = None
    frame_multithreading: bool = False
    slice_multithreading: bool = False
    experimental: bool = False
    hardware: bool = False


class ContainerTool(BaseModel):
    name: str
    description: str
    demux: bool = False
    mux: bool = False
    device: bool = False


class PixelFormatInfo(BaseModel):
    name: str
    input_supported: bool = False
    output_supported: bool = False
    hardware: bool = False
    paletted: bool = False
    bitstream: bool = False
    components: int | None = None
    bits_per_pixel: int | None = None
    bit_depths: str | None = None


class CodecAvailability(BaseModel):
    available: bool
    encoders: list[str] = Field(default_factory=list)
    preferred_encoder: str | None = None
    note: str


class HardwareAcceleration(BaseModel):
    available: bool
    methods: list[str] = Field(default_factory=list)
    hardware_video_encoders: list[str] = Field(default_factory=list)
    note: str


class PresetAvailability(BaseModel):
    available: bool
    encoder: str | None = None
    container: str | None = None
    profile: str | None = None
    pixel_format: str | None = None
    note: str
    detected_hardware_encoders: list[str] = Field(default_factory=list)


class EncoderChoice(BaseModel):
    id: str
    available: bool = True
    kind: Literal["software", "hardware", "unknown"]
    user_visible: bool = True
    hardware_verified: bool = False


class NormalizedCodec(BaseModel):
    id: str
    name: str
    type: Literal["video", "audio"]
    encoders: list[EncoderChoice]
    recommended: bool = False
    default: bool = False
    user_visible: bool = True
    container_compatibility: Literal["not_evaluated"] = "not_evaluated"


class CodecCatalog(BaseModel):
    video: list[NormalizedCodec] = Field(default_factory=list)
    audio: list[NormalizedCodec] = Field(default_factory=list)


class CapabilitySummary(BaseModel):
    ffmpeg_available: bool
    ffprobe_available: bool
    ffmpeg_version: str | None = None
    ffprobe_version: str | None = None
    video_encoder_count: int
    audio_encoder_count: int
    subtitle_encoder_count: int
    video_decoder_count: int
    audio_decoder_count: int
    muxer_count: int
    demuxer_count: int
    pixel_format_count: int
    h264: CodecAvailability
    hevc: CodecAvailability
    aac: CodecAvailability
    hardware_acceleration: HardwareAcceleration


class CapabilitiesResponse(BaseModel):
    summary: CapabilitySummary
    ffmpeg: BinaryStatus
    ffprobe: BinaryStatus
    video_encoders: list[StreamTool]
    audio_encoders: list[StreamTool]
    subtitle_encoders: list[StreamTool]
    video_decoders: list[StreamTool]
    audio_decoders: list[StreamTool]
    subtitle_decoders: list[StreamTool]
    muxers: list[ContainerTool]
    demuxers: list[ContainerTool]
    pixel_formats: list[PixelFormatInfo]
    hardware_acceleration: HardwareAcceleration
    presets: dict[str, PresetAvailability] = Field(default_factory=dict)
    codecs: CodecCatalog = Field(default_factory=CodecCatalog)
    formats: list[dict] = Field(default_factory=list)
    h264_levels: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    phase: str
    ffmpeg_available: bool
    ffprobe_available: bool
    ffmpeg_version: str | None = None
    ffprobe_version: str | None = None
    h264_available: bool
    aac_available: bool
    hevc_available: bool
