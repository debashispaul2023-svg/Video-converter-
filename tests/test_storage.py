"""Storage isolation tests. No media conversion."""

from pathlib import Path

from app.config import Settings
from app.core.security import is_allowed_extension, sanitize_filename
from app.services.storage import LocalStorage


def test_sanitize_filename_blocks_path_traversal() -> None:
    assert sanitize_filename("../../etc/passwd.mp4") == "passwd.mp4"
    assert "/" not in sanitize_filename("/tmp/evil.mp4")
    assert ".." not in sanitize_filename("..\\..\\windows.mp4")
    assert is_allowed_extension("../../clip.mkv") is True
    assert is_allowed_extension("notes.txt") is False
    assert is_allowed_extension("script.sh") is False


def test_job_directory_is_random_and_contained(tmp_path: Path) -> None:
    settings = Settings(
        TEMP_DIRECTORY=str(tmp_path),
        MAX_UPLOAD_SIZE_BYTES=21474836480,
        MIN_FREE_DISK_SPACE_BYTES=1,
    )
    storage = LocalStorage(settings)
    location = storage.create_job("../../outside.mp4")
    assert location.job_id.count("-") == 4
    assert location.directory.parent == tmp_path.resolve()
    assert location.input_path.parent == location.directory
    assert location.input_path.name == "input.mp4"
    assert location.display_name == "outside.mp4"
    assert not (tmp_path / "outside.mp4").exists()
    storage.cleanup(location.job_id)
    assert not location.directory.exists()


def test_job_lookup_rejects_path_like_ids(tmp_path: Path) -> None:
    storage = LocalStorage(Settings(TEMP_DIRECTORY=str(tmp_path), MIN_FREE_DISK_SPACE_BYTES=1))
    assert storage.read_metadata("../secret") is None
    assert storage.read_metadata("not-a-uuid") is None
    assert storage.read_metadata("00000000-0000-0000-0000-000000000000") is None


def test_default_limit_is_twenty_gib_and_timeouts_are_separate() -> None:
    settings = Settings()
    assert settings.max_upload_size_bytes == 21474836480
    assert settings.min_free_disk_space_bytes == 5368709120
    assert settings.upload_timeout_seconds == 21600
    assert settings.ffprobe_timeout_seconds == 30
    assert settings.upload_timeout_seconds != settings.ffprobe_timeout_seconds
    assert not hasattr(settings, "max_upload_size_mb")
