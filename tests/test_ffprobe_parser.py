"""Parser tests for ffprobe metadata. No conversion coverage."""

from app.services.ffprobe import parse_probe_document, parse_rate

PROBE_DOCUMENT = {
    "format": {
        "filename": "/tmp/video-converter/secret/input.mp4",
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "format_long_name": "QuickTime / MOV",
        "duration": "1.000000",
        "bit_rate": "192000",
        "size": "24000",
    },
    "streams": [
        {
            "codec_type": "data",
            "codec_name": "bin_data",
        },
        {
            "codec_type": "video",
            "codec_name": "h264",
            "codec_long_name": "H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10",
            "profile": "Main",
            "width": 160,
            "height": 120,
            "pix_fmt": "yuv420p",
            "avg_frame_rate": "30000/1001",
            "r_frame_rate": "30/1",
            "bit_rate": "128000",
            "nb_frames": "30",
            "disposition": {"attached_pic": 0},
        },
        {
            "codec_type": "video",
            "codec_name": "mjpeg",
            "disposition": {"attached_pic": 1},
            "width": 32,
            "height": 32,
        },
        {
            "codec_type": "audio",
            "codec_name": "aac",
            "codec_long_name": "AAC (Advanced Audio Coding)",
            "sample_rate": "48000",
            "channels": 2,
            "channel_layout": "stereo",
            "bit_rate": "64000",
        },
        {"codec_type": "subtitle", "codec_name": "mov_text"},
    ],
}


def test_parse_rate_handles_fractions_and_invalid_values() -> None:
    assert parse_rate("30000/1001") == 30000 / 1001
    assert parse_rate("30/1") == 30
    assert parse_rate("0/0") is None
    assert parse_rate("N/A") is None
    assert parse_rate(None) is None
    assert parse_rate("not-a-rate") is None


def test_parse_probe_document_selects_primary_streams() -> None:
    media = parse_probe_document(PROBE_DOCUMENT, filename="clip.mp4", size_bytes=24000)
    assert media.duration_seconds == 1.0
    assert media.container == "mov,mp4,m4a,3gp,3g2,mj2"
    assert media.format_long_name == "QuickTime / MOV"
    assert media.bitrate == 192000
    assert media.video is not None
    assert media.video.codec == "h264"
    assert media.video.profile == "Main"
    assert media.video.width == 160
    assert media.video.height == 120
    assert media.video.pixel_format == "yuv420p"
    assert media.video.fps is not None
    assert round(media.video.fps, 2) == 29.97
    assert media.video.frame_count == 30
    assert media.audio is not None
    assert media.audio.codec == "aac"
    assert media.audio.sample_rate == 48000
    assert media.audio.channels == 2
    assert media.audio.channel_layout == "stereo"
    assert media.stream_counts.video == 1
    assert media.stream_counts.audio == 1
    assert media.stream_counts.subtitle == 1
    assert "secret" not in media.model_dump_json()


def test_malformed_probe_document_does_not_crash() -> None:
    media = parse_probe_document(
        {"format": {"duration": "bad"}, "streams": [{"codec_type": "video", "avg_frame_rate": "nope"}]},
        filename="broken.mp4",
        size_bytes=4,
    )
    assert media.duration_seconds is None
    assert media.video is not None
    assert media.video.fps is None
    assert media.audio is None

    empty = parse_probe_document({}, filename="empty.mp4", size_bytes=0)
    assert empty.video is None
    assert empty.audio is None
    assert empty.duration_seconds is None
