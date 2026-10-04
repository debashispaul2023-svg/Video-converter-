"""Redis-backed job queue. The API enqueues job ids; it does not run FFmpeg."""

from __future__ import annotations

import json
from typing import Any

from app.config import Settings

QUEUE_KEY = "video-converter:jobs"
CANCEL_PREFIX = "video-converter:cancel:"
ROOT_PREFIX = "video-converter:root:"
PROGRESS_PREFIX = "video-converter:progress:"


class QueueUnavailable(Exception):
    def __init__(self) -> None:
        super().__init__("Conversion queue is temporarily unavailable.")


class JobQueue:
    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def connect(cls, settings: Settings) -> "JobQueue":
        url = settings.redis_url
        try:
            if url.startswith("fakeredis://"):
                import fakeredis

                client = fakeredis.FakeRedis(decode_responses=True)
            else:
                import redis

                client = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
            client.ping()
        except Exception as exc:
            raise QueueUnavailable() from exc
        return cls(client)

    def enqueue(self, job_id: str, temp_directory: str) -> None:
        try:
            self.client.set(ROOT_PREFIX + job_id, temp_directory, ex=86400)
            self.client.lpush(QUEUE_KEY, job_id)
        except Exception as exc:
            raise QueueUnavailable() from exc

    def dequeue(self, timeout: int = 2) -> str | None:
        try:
            item = self.client.brpop(QUEUE_KEY, timeout=timeout)
        except Exception as exc:
            raise QueueUnavailable() from exc
        if not item:
            return None
        return item[1]

    def job_root(self, job_id: str) -> str | None:
        try:
            return self.client.get(ROOT_PREFIX + job_id)
        except Exception as exc:
            raise QueueUnavailable() from exc

    def remove(self, job_id: str) -> None:
        try:
            self.client.lrem(QUEUE_KEY, 0, job_id)
        except Exception as exc:
            raise QueueUnavailable() from exc

    def request_cancel(self, job_id: str) -> None:
        try:
            self.client.set(CANCEL_PREFIX + job_id, "1", ex=86400)
        except Exception as exc:
            raise QueueUnavailable() from exc

    def cancel_requested(self, job_id: str) -> bool:
        try:
            return self.client.get(CANCEL_PREFIX + job_id) == "1"
        except Exception:
            return False

    def write_progress(self, job_id: str, payload: dict) -> None:
        try:
            self.client.set(PROGRESS_PREFIX + job_id, json.dumps(payload), ex=86400)
        except Exception:
            return

    def read_progress(self, job_id: str) -> dict | None:
        try:
            raw = self.client.get(PROGRESS_PREFIX + job_id)
        except Exception:
            return None
        if not raw:
            return None
        return json.loads(raw)


_queue: JobQueue | None = None


def get_queue(settings: Settings) -> JobQueue:
    global _queue
    if _queue is None:
        _queue = JobQueue.connect(settings)
    return _queue


def reset_queue() -> None:
    global _queue
    _queue = None
