"""FastAPI dependencies."""

from fastapi import Request

from app.config import Settings, get_settings
from app.schemas.capabilities import CapabilitiesResponse
from app.services.ffmpeg_detector import FFmpegDetector


def settings_dependency() -> Settings:
    return get_settings()


def capabilities_dependency(request: Request) -> CapabilitiesResponse:
    cached = getattr(request.app.state, "capabilities", None)
    if cached is None:
        cached = FFmpegDetector(get_settings()).detect()
        request.app.state.capabilities = cached
    return cached
