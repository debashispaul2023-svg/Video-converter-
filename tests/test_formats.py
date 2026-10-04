"""Format, level, and container compatibility tests."""

from app.services.formats import container_allows, format_catalog, level_fits


def test_h264_level_and_container_rules() -> None:
    assert level_fits("4.2", 1920, 1080, 60)
    assert not level_fits("3.0", 1920, 1080, 30)
    assert container_allows("mp4", "h264", "aac")
    assert not container_allows("webm", "h264", "aac")
    assert not container_allows("webm", "vp9", "aac")
    hidden = format_catalog(set(), set())
    assert all(item["available"] is False for item in hidden)
    present = format_catalog({"libvpx-vp9", "libaom-av1"}, {"libopus"})
    available = {item["id"] for item in present if item["available"]}
    assert "vp9_webm" in available
    assert "av1_webm" in available
    assert "mpeg2_ts" not in available
