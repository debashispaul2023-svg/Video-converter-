"""Start a test worker so queued conversions still finish in the suite."""

import os
import threading

os.environ.setdefault("REDIS_URL", "fakeredis://")

from app.config import get_settings
from app.workers.conversion_worker import process_available

_stop = threading.Event()


def _loop() -> None:
    settings = get_settings()
    while not _stop.is_set():
        try:
            process_available(settings, once=True)
        except Exception:
            _stop.wait(0.2)


threading.Thread(target=_loop, name="test-conversion-worker", daemon=True).start()
