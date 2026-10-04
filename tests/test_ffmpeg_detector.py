"""Parser and live-detection tests for Phase 1.

Fixture tests prove the detector does not invent encoders. Live tests run only
when ffmpeg and ffprobe are installed.
"""

from app.config import Settings
from app.services.ffmpeg_detector import (
    FFmpegDetector,
    parse_containers,
    parse_hwaccels,
    parse_pixel_formats,
    parse_stream_tools,
    parse_version_line,
)

ENCODER_FIXTURE = """
Encoders:
 V..... = Video
 A..... = Audio
 S..... = Subtitle
 .F.... = Frame-level multithreading
 ..S... = Slice-level multithreading
 ...X.. = Codec is experimental
 ....B. = Supports draw_horiz_band
 .....D = Supports direct rendering method 1
 ------
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (codec h264)
 V..... h264_vaapi           H.264/AVC (VAAPI) (codec h264)
 A....D aac                  AAC (Advanced Audio Coding)
 A....D libmp3lame           libmp3lame MP3 (MPEG audio layer 3) (codec mp3)
 V..... libx265              libx265 H.265 / HEVC (codec hevc)
"""

ENCODER_FIXTURE_NO_HEVC = """
 V....D libx264              libx264 H.264 / AVC (codec h264)
 A....D aac                  AAC (Advanced Audio Coding)
 V..... libvpx-vp9           libvpx VP9 (codec vp9)
"""

MUXER_FIXTURE = """
Formats:
 D.. = Demuxing supported
 .E. = Muxing supported
 ..d = Is a device
 ---
  E  mp4             MP4 (MPEG-4 Part 14)
  E  matroska        Matroska
 D   aac             raw ADTS AAC (Advanced Audio Coding)
"""

PIXEL_FIXTURE = """
Pixel formats:
I.... = Supported Input  format for conversion
.O... = Supported Output format for conversion
..H.. = Hardware accelerated format
...P. = Paletted format
....B = Bitstream format
FLAGS NAME            NB_COMPONENTS BITS_PER_PIXEL BIT_DEPTHS
-----
IO... yuv420p                3             12      8-8-8
IOH.. nv12                   3             12      8-8-8
"""

HWACCEL_FIXTURE = """
Hardware acceleration methods:
vaapi
cuda
"""


def test_parse_version_line() -> None:
    text = "ffmpeg version 7.0.2-static https://johnvansickle.com/ffmpeg/\n"
    assert parse_version_line(text) == "7.0.2-static"


def test_parse_encoders_reports_only_listed_tools() -> None:
    tools = parse_stream_tools(ENCODER_FIXTURE)
    names = [tool.name for tool in tools]
    assert names == ["libx264", "h264_vaapi", "aac", "libmp3lame", "libx265"]
    assert "libsvtav1" not in names
    assert "not_a_real_encoder" not in names
    by_name = {tool.name: tool for tool in tools}
    assert by_name["libx264"].codec == "h264"
    assert by_name["libx264"].media_type == "video"
    assert by_name["libx264"].hardware is False
    assert by_name["h264_vaapi"].hardware is True
    assert by_name["aac"].media_type == "audio"
    assert by_name["libx265"].codec == "hevc"


def test_h264_aac_available_and_hevc_absent_when_not_listed() -> None:
    detector = FFmpegDetector(Settings())
    tools = parse_stream_tools(ENCODER_FIXTURE_NO_HEVC)
    video = [tool for tool in tools if tool.media_type == "video"]
    audio = [tool for tool in tools if tool.media_type == "audio"]
    h264 = detector._codec_availability(
        video,
        family="h264",
        preferred=("libx264",),
        name_prefixes=("h264_",),
        extra_names=("libx264", "libx264rgb"),
        label="H.264 / AVC",
    )
    hevc = detector._codec_availability(
        video,
        family="hevc",
        preferred=("libx265",),
        name_prefixes=("hevc_",),
        extra_names=("libx265",),
        label="H.265 / HEVC",
    )
    aac = detector._codec_availability(
        audio,
        family="aac",
        preferred=("aac", "libfdk_aac"),
        name_prefixes=(),
        extra_names=("aac", "libfdk_aac"),
        label="AAC",
    )
    assert h264.available is True
    assert h264.preferred_encoder == "libx264"
    assert aac.available is True
    assert aac.preferred_encoder == "aac"
    assert hevc.available is False
    assert hevc.encoders == []
    assert hevc.preferred_encoder is None
    assert "libvpx-vp9" not in hevc.encoders


def test_parse_muxers_ignores_demux_only_rows() -> None:
    tools = parse_containers(MUXER_FIXTURE)
    muxers = [tool.name for tool in tools if tool.mux]
    assert muxers == ["mp4", "matroska"]
    assert "aac" not in muxers


def test_parse_pixel_formats_and_hwaccels() -> None:
    pixels = parse_pixel_formats(PIXEL_FIXTURE)
    assert [item.name for item in pixels] == ["yuv420p", "nv12"]
    assert pixels[0].output_supported is True
    assert pixels[0].hardware is False
    assert pixels[1].hardware is True
    assert parse_hwaccels(HWACCEL_FIXTURE) == ["vaapi", "cuda"]
    assert parse_hwaccels("Hardware acceleration methods:\n") == []


def test_missing_binary_is_not_available(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.ffmpeg_detector.shutil.which", lambda _name: None
    )
    settings = Settings(FFMPEG_BINARY="ffmpeg-missing-bin", FFPROBE_BINARY="ffprobe-missing-bin")
    detected = FFmpegDetector(settings).detect()
    assert detected.ffmpeg.available is False
    assert detected.ffprobe.available is False
    assert detected.summary.h264.available is False
    assert detected.summary.aac.available is False
    assert detected.summary.hevc.available is False
    assert detected.video_encoders == []
    assert "not available" in (detected.ffmpeg.error or "")


def test_live_detection_matches_installed_ffmpeg() -> None:
    import shutil

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        return
    detected = FFmpegDetector(Settings()).detect()
    assert detected.ffmpeg.available is True
    assert detected.ffprobe.available is True
    assert detected.ffmpeg.version
    assert detected.ffprobe.version
    names = {tool.name for tool in detected.video_encoders}
    audio_names = {tool.name for tool in detected.audio_encoders}
    assert "not_a_real_encoder" not in names
    assert "definitely_missing_codec" not in audio_names
    # This environment's static FFmpeg advertises these. If a deployment lacks
    # them, the assertion below still forbids inventing a name that was not listed.
    if detected.summary.h264.available:
        assert detected.summary.h264.preferred_encoder in names
    if detected.summary.hevc.available:
        assert detected.summary.hevc.preferred_encoder in names
    if detected.summary.aac.available:
        assert detected.summary.aac.preferred_encoder in audio_names
    assert detected.summary.video_encoder_count == len(detected.video_encoders)
    assert any(item.name == "yuv420p" for item in detected.pixel_formats)
