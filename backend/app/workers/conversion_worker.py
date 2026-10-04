"""Worker entrypoint. Run separately from the API process."""

from __future__ import annotations

import logging
import time

from app.config import get_settings
from app.core.security import is_job_id
from app.services.converter import ConversionService
from app.services.ffmpeg_detector import FFmpegDetector
from app.services.ffprobe import FFProbeService
from app.services.queue import QueueUnavailable, get_queue
from app.services.storage import LocalStorage

logger = logging.getLogger(__name__)


def process_available(settings=None, *, once: bool = False) -> None:
    settings = settings or get_settings()
    queue = get_queue(settings)
    storage = LocalStorage(settings)
    probe = FFProbeService(settings)
    capabilities = FFmpegDetector(settings).detect()
    service = ConversionService(settings, storage, probe, capabilities)
    while True:
        try:
            job_id = queue.dequeue(timeout=1)
        except QueueUnavailable:
            logger.error("queue unavailable")
            if once:
                return
            time.sleep(2)
            continue
        if job_id:
            if not is_job_id(job_id):
                logger.warning("ignored malformed queue entry")
                continue
            logger.info("queue start job_id=%s", job_id)
            try:
                root = queue.job_root(job_id) or settings.temp_directory
                job_settings = settings.model_copy(update={"temp_directory": root})
                storage = LocalStorage(job_settings)
                service = ConversionService(job_settings, storage, probe, capabilities)
                service.execute(job_id)
            except Exception:
                logger.exception("worker job failed job_id=%s", job_id)
        try:
            cleanup_expired(storage, settings.job_retention_seconds)
        except Exception:
            logger.exception("cleanup failure")
        if once and not job_id:
            return


def recover_orphans(storage: LocalStorage, queue) -> None:
    """Requeue crashed queued jobs. Mark crashed processing jobs failed and remove partial output."""

    if not storage.root.exists():
        return
    for directory in storage.root.iterdir():
        if not directory.is_dir() or not is_job_id(directory.name):
            continue
        payload = storage.read_metadata(directory.name)
        if payload is None:
            continue
        status = payload.get("status")
        partial = directory / "output.partial.mp4"
        if status == "processing":
            partial.unlink(missing_ok=True)
            output = directory / "output.mp4"
            output.unlink(missing_ok=True)
            storage.update_metadata(directory.name, {"status": "failed", "error": "Conversion was interrupted.", "output": None})
            logger.info("recovery failed interrupted job_id=%s", directory.name)
        elif status == "queued" and storage.input_file(directory.name) and payload.get("conversion"):
            try:
                queue.enqueue(directory.name, str(storage.root))
                logger.info("recovery requeued job_id=%s", directory.name)
            except Exception:
                logger.exception("recovery requeue failed job_id=%s", directory.name)


def cleanup_expired(storage: LocalStorage, retention_seconds: int) -> int:
    removed = 0
    root = storage.root
    if not root.exists():
        return 0
    now = time.time()
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        payload = storage.read_metadata(directory.name)
        if payload is None:
            continue
        if payload.get("status") in {"queued", "processing"}:
            continue
        updated = directory.stat().st_mtime
        if now - updated < retention_seconds:
            continue
        storage.update_metadata(directory.name, {"status": "expired"})
        storage.cleanup(directory.name)
        removed += 1
        logger.info("cleanup expired job_id=%s", directory.name)
    return removed


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    logger.info("worker start concurrency=%s", settings.worker_concurrency)
    storage = LocalStorage(settings)
    try:
        recover_orphans(storage, get_queue(settings))
    except Exception:
        logger.exception("startup recovery failed")
    process_available(settings)


if __name__ == "__main__":
    main()
