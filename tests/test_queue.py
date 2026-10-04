"""Queue, progress, cancellation, and retention tests."""

import time
from pathlib import Path

from app.services.progress import parse_progress
from app.services.queue import JobQueue
from app.workers.conversion_worker import cleanup_expired
from app.services.storage import LocalStorage
from app.config import Settings


def test_progress_parser_clamps_and_skips_bad_eta() -> None:
    parsed = parse_progress("out_time_ms=5000000\nspeed=2.0x\nprogress=continue\n", 10)
    assert parsed["percent"] == 50.0
    assert parsed["speed"] == 2.0
    assert parsed["eta_seconds"] == 2.5
    done = parse_progress("out_time_ms=20000000\nprogress=end\n", 10)
    assert done["percent"] == 100.0
    missing = parse_progress("speed=N/A\nprogress=continue\n", None)
    assert missing["percent"] == 0.0
    assert missing["eta_seconds"] is None


def test_queue_round_trip_and_cancel_flag() -> None:
    queue = JobQueue.connect(Settings(REDIS_URL="fakeredis://"))
    queue.enqueue("job-1", "/tmp/video-converter")
    assert queue.dequeue(timeout=1) == "job-1"
    queue.request_cancel("job-1")
    assert queue.cancel_requested("job-1") is True


def test_expired_jobs_are_removed_and_active_jobs_stay(tmp_path: Path) -> None:
    storage = LocalStorage(Settings(TEMP_DIRECTORY=str(tmp_path), MIN_FREE_DISK_SPACE_BYTES=1))
    old = storage.create_job("old.mp4")
    active = storage.create_job("active.mp4")
    storage.write_metadata(old, {"job_id": old.job_id, "status": "completed"})
    storage.write_metadata(active, {"job_id": active.job_id, "status": "processing"})
    old_stamp = time.time() - 100
    os_utime = __import__("os").utime
    os_utime(old.directory, (old_stamp, old_stamp))
    os_utime(old.metadata_path, (old_stamp, old_stamp))
    removed = cleanup_expired(storage, 10)
    assert removed == 1
    assert not old.directory.exists()
    assert active.directory.exists()
