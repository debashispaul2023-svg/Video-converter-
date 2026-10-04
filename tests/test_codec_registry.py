"""Codec registry tests. Detection fixtures do not create conversion presets."""

from app.schemas.capabilities import StreamTool
from app.services.codec_registry import build_codec_catalog
from app.services.presets import PRESETS


def _encoder(name: str, media_type: str, codec: str, hardware: bool = False) -> StreamTool:
    return StreamTool(
        name=name,
        description=name,
        media_type=media_type,
        codec=codec,
        hardware=hardware,
    )


def test_video_and_audio_names_are_normalized() -> None:
    catalog = build_codec_catalog(
        [
            _encoder("libx264", "video", "h264"),
            _encoder("libx265", "video", "hevc"),
            _encoder("libvpx-vp9", "video", "vp9"),
            _encoder("libvpx", "video", "vp8"),
            _encoder("libaom-av1", "video", "av1"),
        ],
        [
            _encoder("aac", "audio", "aac"),
            _encoder("libmp3lame", "audio", "mp3"),
            _encoder("libopus", "audio", "opus"),
            _encoder("libvorbis", "audio", "vorbis"),
            _encoder("flac", "audio", "flac"),
        ],
    )
    video = {item.id: item for item in catalog.video}
    audio = {item.id: item for item in catalog.audio}
    assert video["h264"].name == "H.264"
    assert video["hevc"].name == "H.265 / HEVC"
    assert video["vp9"].name == "VP9"
    assert video["vp8"].name == "VP8"
    assert video["av1"].name == "AV1"
    assert audio["aac"].name == "AAC"
    assert audio["mp3"].name == "MP3"
    assert audio["opus"].name == "Opus"
    assert audio["vorbis"].name == "Vorbis"
    assert audio["flac"].name == "FLAC"
    assert [item.id for item in video["h264"].encoders] == ["libx264"]


def test_missing_encoder_is_not_available_and_decoders_are_ignored() -> None:
    catalog = build_codec_catalog(
        [_encoder("libx264", "video", "h264")],
        [_encoder("aac", "audio", "aac")],
    )
    assert "hevc" not in {item.id for item in catalog.video}
    assert "alac" not in {item.id for item in catalog.audio}
    assert "vp9" not in {item.id for item in catalog.video}
    decoder_only = build_codec_catalog([], [])
    assert decoder_only.video == []
    assert decoder_only.audio == []


def test_multiple_encoders_group_and_hardware_is_unverified() -> None:
    catalog = build_codec_catalog(
        [
            _encoder("h264_v4l2m2m", "video", "h264", hardware=True),
            _encoder("libx264", "video", "h264"),
            _encoder("pcm_s16le", "audio", "pcm_s16le"),
        ],
        [
            _encoder("aac", "audio", "aac"),
            _encoder("pcm_s16le", "audio", "pcm_s16le"),
            _encoder("pcm_s24le", "audio", "pcm_s24le"),
        ],
    )
    h264 = next(item for item in catalog.video if item.id == "h264")
    assert [item.id for item in h264.encoders] == ["libx264", "h264_v4l2m2m"]
    assert h264.encoders[0].kind == "software"
    assert h264.encoders[1].kind == "hardware"
    assert h264.encoders[1].hardware_verified is False
    assert h264.recommended is True
    pcm = next(item for item in catalog.audio if item.id == "pcm")
    assert pcm.name == "PCM"
    assert {item.id for item in pcm.encoders} == {"pcm_s16le", "pcm_s24le"}


def test_aac_is_default_only_when_native_encoder_exists() -> None:
    present = build_codec_catalog([], [_encoder("aac", "audio", "aac")])
    aac = present.audio[0]
    assert aac.recommended is True
    assert aac.default is True
    missing = build_codec_catalog([], [_encoder("libmp3lame", "audio", "mp3")])
    assert all(not item.default for item in missing.audio)
    assert "aac" not in {item.id for item in missing.audio}


def test_registry_does_not_add_conversion_presets() -> None:
    assert set(PRESETS) == {
        "h264_baseline",
        "h264_main",
        "h264_high",
        "h264_high10",
        "h265_hevc",
    }
