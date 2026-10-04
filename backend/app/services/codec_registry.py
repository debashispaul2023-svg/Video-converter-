"""Normalize detected FFmpeg encoders into codec groups.

This registry names encoders. It does not create conversion presets and it does
not treat a decoder as an encoder. Container compatibility is intentionally
unevaluated so a later phase can add it.
"""

from __future__ import annotations

from app.schemas.capabilities import CodecCatalog, EncoderChoice, NormalizedCodec, StreamTool

_FRIENDLY_NAMES = {
    "h264": "H.264",
    "hevc": "H.265 / HEVC",
    "vp8": "VP8",
    "vp9": "VP9",
    "av1": "AV1",
    "mpeg4": "MPEG-4",
    "mpeg2video": "MPEG-2",
    "mpeg1video": "MPEG-1",
    "prores": "ProRes",
    "dnxhd": "DNxHD/DNxHR",
    "theora": "Theora",
    "ffv1": "FFV1",
    "aac": "AAC",
    "mp3": "MP3",
    "ac3": "AC-3",
    "eac3": "E-AC-3",
    "mp2": "MP2",
    "opus": "Opus",
    "vorbis": "Vorbis",
    "flac": "FLAC",
    "alac": "ALAC",
    "pcm": "PCM",
}

# Naming only. An entry here does not mean the encoder exists.
_ENCODER_CODEC = {
    "libx264": "h264",
    "libx264rgb": "h264",
    "libx265": "hevc",
    "libvpx": "vp8",
    "libvpx-vp9": "vp9",
    "libaom-av1": "av1",
    "libsvtav1": "av1",
    "librav1e": "av1",
    "aac": "aac",
    "libfdk_aac": "aac",
    "libmp3lame": "mp3",
    "libopus": "opus",
    "libvorbis": "vorbis",
    "flac": "flac",
    "alac": "alac",
    "ac3": "ac3",
    "eac3": "eac3",
    "mp2": "mp2",
    "libtwolame": "mp2",
    "mpeg4": "mpeg4",
    "libxvid": "mpeg4",
    "mpeg2video": "mpeg2video",
    "mpeg1video": "mpeg1video",
    "prores": "prores",
    "prores_ks": "prores",
    "prores_aw": "prores",
    "dnxhd": "dnxhd",
    "libtheora": "theora",
    "ffv1": "ffv1",
}

_INTERNAL_CODEC_IDS = {
    "png",
    "apng",
    "mjpeg",
    "gif",
    "webp",
    "bmp",
    "tiff",
    "ljpeg",
    "jpeg2000",
    "jpegls",
    "dpx",
    "exr",
    "alias_pix",
    "amv",
    "a64_multi",
    "rawvideo",
    "wrapped_avframe",
    "vnull",
    "anull",
    "bitpacked",
}

_PREFERRED_ENCODER = {
    "h264": ("libx264",),
    "hevc": ("libx265",),
    "aac": ("aac", "libfdk_aac"),
    "vp9": ("libvpx-vp9",),
    "vp8": ("libvpx",),
    "av1": ("libaom-av1", "libsvtav1"),
    "mp3": ("libmp3lame",),
    "opus": ("libopus",),
    "vorbis": ("libvorbis",),
}


def build_codec_catalog(
    video_encoders: list[StreamTool],
    audio_encoders: list[StreamTool],
) -> CodecCatalog:
    return CodecCatalog(
        video=_group(video_encoders, "video"),
        audio=_group(audio_encoders, "audio"),
    )


def _group(encoders: list[StreamTool], media_type: str) -> list[NormalizedCodec]:
    grouped: dict[str, list[EncoderChoice]] = {}
    order: list[str] = []
    for encoder in encoders:
        if encoder.media_type != media_type:
            continue
        codec_id = _codec_id(encoder)
        if codec_id not in grouped:
            grouped[codec_id] = []
            order.append(codec_id)
        choice = _choice(encoder)
        if any(item.id == choice.id for item in grouped[codec_id]):
            continue
        grouped[codec_id].append(choice)
    codecs = []
    for codec_id in order:
        choices = _sort_encoders(codec_id, grouped[codec_id])
        visible_encoders = [item for item in choices if item.user_visible]
        codec_visible = codec_id in _FRIENDLY_NAMES and bool(visible_encoders)
        codecs.append(
            NormalizedCodec(
                id=codec_id,
                name=_FRIENDLY_NAMES.get(codec_id, codec_id.upper()),
                type=media_type,
                encoders=choices,
                recommended=_recommended(codec_id, choices),
                default=_default(codec_id, choices),
                user_visible=codec_visible,
            )
        )
    return codecs


def _codec_id(encoder: StreamTool) -> str:
    if encoder.name in _ENCODER_CODEC:
        return _ENCODER_CODEC[encoder.name]
    if encoder.name.startswith("pcm_"):
        return "pcm"
    if encoder.name.startswith("h264_"):
        return "h264"
    if encoder.name.startswith("hevc_"):
        return "hevc"
    if encoder.codec:
        return encoder.codec
    return encoder.name


def _choice(encoder: StreamTool) -> EncoderChoice:
    kind = "hardware" if encoder.hardware else "software" if encoder.name.startswith("lib") or encoder.name in _ENCODER_CODEC else "unknown"
    if encoder.name in {"aac", "flac", "alac", "ac3", "eac3", "mp2", "ffv1", "mpeg4", "prores", "dnxhd"}:
        kind = "software"
    return EncoderChoice(
        id=encoder.name,
        available=True,
        kind=kind,
        user_visible=not encoder.name.startswith("a64"),
        hardware_verified=False,
    )


def _sort_encoders(codec_id: str, choices: list[EncoderChoice]) -> list[EncoderChoice]:
    preferred = _PREFERRED_ENCODER.get(codec_id, ())

    def rank(choice: EncoderChoice) -> tuple[int, str]:
        if choice.id in preferred:
            return (preferred.index(choice.id), choice.id)
        if choice.kind == "software":
            return (10, choice.id)
        if choice.kind == "hardware":
            return (20, choice.id)
        return (30, choice.id)

    return sorted(choices, key=rank)


def _recommended(codec_id: str, choices: list[EncoderChoice]) -> bool:
    names = {item.id for item in choices}
    if codec_id == "aac":
        return "aac" in names
    if codec_id == "h264":
        return "libx264" in names
    return False


def _default(codec_id: str, choices: list[EncoderChoice]) -> bool:
    return codec_id == "aac" and any(item.id == "aac" for item in choices)
