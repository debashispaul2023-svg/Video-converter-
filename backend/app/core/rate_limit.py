"""Small IP rate limiter. This is abuse control, not authentication."""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import HTTPException, Request

_hits: dict[tuple[str, str], list[float]] = defaultdict(list)


def limit(request: Request, bucket: str, maximum: int, window_seconds: int = 60) -> None:
    if maximum <= 0:
        return
    now = time.monotonic()
    key = (request.client.host if request.client else "unknown", bucket)
    recent = [stamp for stamp in _hits[key] if now - stamp < window_seconds]
    if len(recent) >= maximum:
        raise HTTPException(status_code=429, detail="Too many requests. Try again shortly.")
    recent.append(now)
    _hits[key] = recent
