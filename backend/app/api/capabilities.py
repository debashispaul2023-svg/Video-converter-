"""Capability endpoint. Lists only tools the installed FFmpeg reported."""

from fastapi import APIRouter, Depends

from app.config import get_settings
from app.dependencies import capabilities_dependency
from app.schemas.capabilities import CapabilitiesResponse
from app.services.codec_registry import build_codec_catalog
from app.services.formats import H264_LEVELS, format_catalog
from app.services.presets import preset_catalog

router = APIRouter(tags=["capabilities"])


@router.get("/capabilities", response_model=CapabilitiesResponse)
def capabilities(
    detected: CapabilitiesResponse = Depends(capabilities_dependency),
) -> CapabilitiesResponse:
    encoders = {item.name for item in detected.video_encoders}
    audio = {item.name for item in detected.audio_encoders}
    return detected.model_copy(
        update={
            "presets": preset_catalog(detected, ffmpeg_binary=get_settings().ffmpeg_binary),
            "codecs": build_codec_catalog(detected.video_encoders, detected.audio_encoders),
            "formats": format_catalog(encoders, audio),
            "h264_levels": ["auto", *H264_LEVELS],
        }
    )
