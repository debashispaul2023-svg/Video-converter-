"""Detect FFmpeg and ffprobe capabilities from the installed binaries.

Availability is derived only from command output. This module never marks an
encoder, decoder, muxer, or hardware method as present unless FFmpeg listed it.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass

from app.config import Settings
from app.schemas.capabilities import (
    BinaryStatus,
    CapabilitiesResponse,
    CapabilitySummary,
    CodecAvailability,
    ContainerTool,
    HardwareAcceleration,
    PixelFormatInfo,
    StreamTool,
)

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"^(?:ffmpeg|ffprobe) version (\S+)", re.IGNORECASE)
_CONFIG_RE = re.compile(r"^configuration:\s*(.+)$", re.IGNORECASE)
_STREAM_RE = re.compile(
    r"^\s([VAS])([.F])([.S])([.X])([.B])([.D])\s+([A-Za-z0-9][A-Za-z0-9_\-]*)\s+(.*)$"
)
_CONTAINER_RE = re.compile(
    r"^\s([D ])([E ])([d ])\s+(\S+)\s+(.*)$"
)
_PIXEL_RE = re.compile(
    r"^\s*([I.])([O.])([H.])([P.])([B.])\s+(\S+)\s+(\d+)\s+(\d+)\s+(\S+)"
)
_CODEC_RE = re.compile(r"\(codec ([^)]+)\)\s*$")

# Name fragments that identify hardware encoders/decoders in FFmpeg builds.
# A tool is hardware only if its name matches one of these or -hwaccels listed
# a method and the encoder name embeds that method. Unknown names stay software.
_HW_NAME_MARKERS = (
    "_nvenc",
    "_cuvid",
    "_vaapi",
    "_qsv",
    "_amf",
    "_videotoolbox",
    "_v4l2m2m",
    "_mediacodec",
    "_omx",
    "_vulkan",
    "_mf",
    "_d3d11va",
    "_dxva2",
    "_vdpau",
)

_H264_PREFERRED = (
    "libx264",
    "h264_nvenc",
    "h264_qsv",
    "h264_vaapi",
    "h264_videotoolbox",
    "h264_amf",
    "h264_v4l2m2m",
    "libx264rgb",
)
_HEVC_PREFERRED = (
    "libx265",
    "hevc_nvenc",
    "hevc_qsv",
    "hevc_vaapi",
    "hevc_videotoolbox",
    "hevc_amf",
    "hevc_v4l2m2m",
)
_AAC_PREFERRED = ("aac", "libfdk_aac")


@dataclass
class CommandOutput:
    ok: bool
    stdout: str
    stderr: str
    error: str | None = None


class FFmpegDetector:
    """Probe a specific FFmpeg/ffprobe pair and build a capability map."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.timeout = settings.capability_probe_timeout_seconds

    def detect(self) -> CapabilitiesResponse:
        ffmpeg_path = self._resolve_binary(self.settings.ffmpeg_binary)
        ffprobe_path = self._resolve_binary(self.settings.ffprobe_binary)

        ffmpeg_version = self._probe_version(ffmpeg_path, "ffmpeg")
        ffprobe_version = self._probe_version(ffprobe_path, "ffprobe")

        encoders = self._probe_stream_tools(ffmpeg_path, "-encoders")
        decoders = self._probe_stream_tools(ffmpeg_path, "-decoders")
        muxers = self._probe_containers(ffmpeg_path, "-muxers", require_mux=True)
        demuxers = self._probe_containers(ffmpeg_path, "-demuxers", require_demux=True)
        pixel_formats = self._probe_pixel_formats(ffmpeg_path)
        hw_methods = self._probe_hwaccels(ffmpeg_path)

        video_encoders = [item for item in encoders if item.media_type == "video"]
        audio_encoders = [item for item in encoders if item.media_type == "audio"]
        subtitle_encoders = [item for item in encoders if item.media_type == "subtitle"]
        video_decoders = [item for item in decoders if item.media_type == "video"]
        audio_decoders = [item for item in decoders if item.media_type == "audio"]
        subtitle_decoders = [item for item in decoders if item.media_type == "subtitle"]

        h264 = self._codec_availability(
            video_encoders,
            family="h264",
            preferred=_H264_PREFERRED,
            name_prefixes=("h264_",),
            extra_names=("libx264", "libx264rgb"),
            label="H.264 / AVC",
        )
        hevc = self._codec_availability(
            video_encoders,
            family="hevc",
            preferred=_HEVC_PREFERRED,
            name_prefixes=("hevc_",),
            extra_names=("libx265",),
            label="H.265 / HEVC",
        )
        aac = self._codec_availability(
            audio_encoders,
            family="aac",
            preferred=_AAC_PREFERRED,
            name_prefixes=(),
            extra_names=("aac", "libfdk_aac"),
            label="AAC",
        )
        hardware = self._hardware(hw_methods, video_encoders)

        summary = CapabilitySummary(
            ffmpeg_available=ffmpeg_version.available,
            ffprobe_available=ffprobe_version.available,
            ffmpeg_version=ffmpeg_version.version,
            ffprobe_version=ffprobe_version.version,
            video_encoder_count=len(video_encoders),
            audio_encoder_count=len(audio_encoders),
            subtitle_encoder_count=len(subtitle_encoders),
            video_decoder_count=len(video_decoders),
            audio_decoder_count=len(audio_decoders),
            muxer_count=len(muxers),
            demuxer_count=len(demuxers),
            pixel_format_count=len(pixel_formats),
            h264=h264,
            hevc=hevc,
            aac=aac,
            hardware_acceleration=hardware,
        )
        return CapabilitiesResponse(
            summary=summary,
            ffmpeg=ffmpeg_version,
            ffprobe=ffprobe_version,
            video_encoders=video_encoders,
            audio_encoders=audio_encoders,
            subtitle_encoders=subtitle_encoders,
            video_decoders=video_decoders,
            audio_decoders=audio_decoders,
            subtitle_decoders=subtitle_decoders,
            muxers=muxers,
            demuxers=demuxers,
            pixel_formats=pixel_formats,
            hardware_acceleration=hardware,
        )

    def _resolve_binary(self, configured: str) -> str | None:
        if not configured or configured.strip() != configured or "/" in configured[0:1]:
            # Absolute paths are allowed only when the file is executable.
            pass
        candidate = configured.strip()
        if not candidate or any(sep in candidate for sep in ("\n", "\r", "\x00")):
            logger.error("Rejected unsafe binary setting")
            return None
        if "/" in candidate:
            return candidate if shutil.which(candidate) or _is_executable(candidate) else None
        return shutil.which(candidate)

    def _probe_version(self, binary: str | None, expected_name: str) -> BinaryStatus:
        if binary is None:
            logger.warning("%s binary is not available", expected_name)
            return BinaryStatus(
                name=expected_name,
                available=False,
                error=f"{expected_name} is not available on this server",
            )
        result = self._run(binary, ["-version"])
        if not result.ok and not result.stdout:
            logger.error("%s -version failed: %s", expected_name, result.error)
            return BinaryStatus(
                name=expected_name,
                available=False,
                error=f"{expected_name} could not be executed",
            )
        text = result.stdout or result.stderr
        version = None
        configuration = None
        for line in text.splitlines():
            if version is None:
                match = _VERSION_RE.match(line.strip())
                if match:
                    version = match.group(1)
            if configuration is None:
                config_match = _CONFIG_RE.match(line.strip())
                if config_match:
                    configuration = config_match.group(1).strip()
        if version is None:
            return BinaryStatus(
                name=expected_name,
                available=False,
                error=f"{expected_name} version output could not be parsed",
            )
        return BinaryStatus(
            name=expected_name,
            available=True,
            version=version,
            build_configuration=configuration,
        )

    def _probe_stream_tools(self, binary: str | None, flag: str) -> list[StreamTool]:
        if binary is None:
            return []
        result = self._run(binary, [flag])
        if not result.stdout:
            logger.error("No output from ffmpeg %s (%s)", flag, result.error)
            return []
        return parse_stream_tools(result.stdout)

    def _probe_containers(
        self,
        binary: str | None,
        flag: str,
        *,
        require_mux: bool = False,
        require_demux: bool = False,
    ) -> list[ContainerTool]:
        if binary is None:
            return []
        result = self._run(binary, [flag])
        if not result.stdout:
            logger.error("No output from ffmpeg %s (%s)", flag, result.error)
            return []
        tools = parse_containers(result.stdout)
        if require_mux:
            tools = [item for item in tools if item.mux]
        if require_demux:
            tools = [item for item in tools if item.demux]
        return tools

    def _probe_pixel_formats(self, binary: str | None) -> list[PixelFormatInfo]:
        if binary is None:
            return []
        result = self._run(binary, ["-pix_fmts"])
        if not result.stdout:
            logger.error("No output from ffmpeg -pix_fmts (%s)", result.error)
            return []
        return parse_pixel_formats(result.stdout)

    def _probe_hwaccels(self, binary: str | None) -> list[str]:
        if binary is None:
            return []
        result = self._run(binary, ["-hwaccels"])
        if not result.stdout:
            logger.error("No output from ffmpeg -hwaccels (%s)", result.error)
            return []
        return parse_hwaccels(result.stdout)

    def _run(self, binary: str, args: list[str]) -> CommandOutput:
        command = [binary, *args]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.error("Capability probe timed out: %s", args)
            return CommandOutput(ok=False, stdout="", stderr="", error="timeout")
        except OSError as exc:
            logger.error("Capability probe failed to start: %s", exc)
            return CommandOutput(ok=False, stdout="", stderr="", error="execution_failed")
        return CommandOutput(
            ok=completed.returncode == 0,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            error=None if completed.returncode == 0 else "nonzero_exit",
        )

    def _codec_availability(
        self,
        tools: list[StreamTool],
        *,
        family: str,
        preferred: tuple[str, ...],
        name_prefixes: tuple[str, ...],
        extra_names: tuple[str, ...],
        label: str,
    ) -> CodecAvailability:
        matched = [
            tool.name
            for tool in tools
            if _matches_family(tool, family, name_prefixes, extra_names)
        ]
        # Preserve FFmpeg order but drop duplicates.
        unique: list[str] = []
        for name in matched:
            if name not in unique:
                unique.append(name)
        preferred_encoder = next((name for name in preferred if name in unique), None)
        if preferred_encoder is None and unique:
            preferred_encoder = unique[0]
        if unique:
            note = f"{label} encoder detected: {', '.join(unique)}"
        else:
            note = f"{label} encoder is not available in the installed FFmpeg"
        return CodecAvailability(
            available=bool(unique),
            encoders=unique,
            preferred_encoder=preferred_encoder,
            note=note,
        )

    def _hardware(
        self, methods: list[str], video_encoders: list[StreamTool]
    ) -> HardwareAcceleration:
        hw_encoders = [tool.name for tool in video_encoders if tool.hardware]
        available = bool(methods or hw_encoders)
        if available:
            note = "Hardware acceleration methods or hardware encoders were detected"
        else:
            note = "No hardware acceleration detected; CPU encoding remains available"
        return HardwareAcceleration(
            available=available,
            methods=methods,
            hardware_video_encoders=hw_encoders,
            note=note,
        )


def parse_stream_tools(text: str) -> list[StreamTool]:
    """Parse `ffmpeg -encoders` or `ffmpeg -decoders` output."""

    tools: list[StreamTool] = []
    for line in text.splitlines():
        match = _STREAM_RE.match(line)
        if not match:
            continue
        media_flag, frame, slice_, experimental, _band, _direct, name, description = match.groups()
        media_type = {"V": "video", "A": "audio", "S": "subtitle"}[media_flag]
        codec_match = _CODEC_RE.search(description)
        codec = codec_match.group(1) if codec_match else None
        tools.append(
            StreamTool(
                name=name,
                description=description.strip(),
                media_type=media_type,
                codec=codec,
                frame_multithreading=frame == "F",
                slice_multithreading=slice_ == "S",
                experimental=experimental == "X",
                hardware=_is_hardware_name(name),
            )
        )
    return tools


def parse_containers(text: str) -> list[ContainerTool]:
    """Parse `ffmpeg -muxers`, `-demuxers`, or `-formats` output."""

    tools: list[ContainerTool] = []
    for line in text.splitlines():
        match = _CONTAINER_RE.match(line)
        if not match:
            continue
        demux, mux, device, name, description = match.groups()
        tools.append(
            ContainerTool(
                name=name,
                description=description.strip(),
                demux=demux == "D",
                mux=mux == "E",
                device=device == "d",
            )
        )
    return tools


def parse_pixel_formats(text: str) -> list[PixelFormatInfo]:
    formats: list[PixelFormatInfo] = []
    for line in text.splitlines():
        match = _PIXEL_RE.match(line)
        if not match:
            continue
        inp, out, hw, pal, bitstream, name, components, bpp, depths = match.groups()
        formats.append(
            PixelFormatInfo(
                name=name,
                input_supported=inp == "I",
                output_supported=out == "O",
                hardware=hw == "H",
                paletted=pal == "P",
                bitstream=bitstream == "B",
                components=int(components),
                bits_per_pixel=int(bpp),
                bit_depths=depths,
            )
        )
    return formats


def parse_hwaccels(text: str) -> list[str]:
    methods: list[str] = []
    started = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("hardware acceleration methods"):
            started = True
            continue
        if not started:
            continue
        if " " in stripped:
            continue
        methods.append(stripped)
    return methods


def parse_version_line(text: str) -> str | None:
    for line in text.splitlines():
        match = _VERSION_RE.match(line.strip())
        if match:
            return match.group(1)
    return None


def _matches_family(
    tool: StreamTool,
    family: str,
    name_prefixes: tuple[str, ...],
    extra_names: tuple[str, ...],
) -> bool:
    if tool.codec == family:
        return True
    if tool.name in extra_names:
        return True
    return any(tool.name.startswith(prefix) for prefix in name_prefixes)


def _is_hardware_name(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in _HW_NAME_MARKERS)


def _is_executable(path: str) -> bool:
    import os

    return os.path.isfile(path) and os.access(path, os.X_OK)
