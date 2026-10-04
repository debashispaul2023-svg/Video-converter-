"""Health and readiness. Health is process liveness. Readiness checks dependencies."""

from fastapi import APIRouter, Depends

from app.config import get_settings
from app.dependencies import capabilities_dependency
from app.schemas.capabilities import CapabilitiesResponse, HealthResponse
from app.services.queue import QueueUnavailable, get_queue
from app.services.storage import LocalStorage

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(
    capabilities: CapabilitiesResponse = Depends(capabilities_dependency),
) -> HealthResponse:
    ffmpeg_ok = capabilities.ffmpeg.available
    ffprobe_ok = capabilities.ffprobe.available
    return HealthResponse(
        status="ok" if ffmpeg_ok and ffprobe_ok else "degraded",
        service="video-converter",
        phase="1-capability-detection",
        ffmpeg_available=ffmpeg_ok,
        ffprobe_available=ffprobe_ok,
        ffmpeg_version=capabilities.ffmpeg.version,
        ffprobe_version=capabilities.ffprobe.version,
        h264_available=capabilities.summary.h264.available,
        aac_available=capabilities.summary.aac.available,
        hevc_available=capabilities.summary.hevc.available,
    )


@router.get("/ready")
def ready(capabilities: CapabilitiesResponse = Depends(capabilities_dependency)) -> dict:
    settings = get_settings()
    storage = LocalStorage(settings)
    storage_ok = False
    try:
        storage.root.mkdir(parents=True, exist_ok=True)
        probe = storage.root / ".ready"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        storage_ok = storage.free_bytes() >= 0
    except OSError:
        storage_ok = False
    redis_ok = False
    try:
        get_queue(settings).client.ping()
        redis_ok = True
    except (QueueUnavailable, Exception):
        redis_ok = False
    ffmpeg_ok = capabilities.ffmpeg.available
    ffprobe_ok = capabilities.ffprobe.available
    ready_ok = ffmpeg_ok and ffprobe_ok and redis_ok and storage_ok
    return {
        "status": "ready" if ready_ok else "not_ready",
        "ffmpeg": ffmpeg_ok,
        "ffprobe": ffprobe_ok,
        "redis": redis_ok,
        "storage": storage_ok,
    }
