"""FastAPI application entrypoint for Phase 1."""

from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.capabilities import router as capabilities_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.config import get_settings
from app.core.logging import configure_logging
from app.services.ffmpeg_detector import FFmpegDetector


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    app.state.capabilities = FFmpegDetector(settings).detect()
    yield


app = FastAPI(
    title="Video Converter",
    version=__version__,
    summary="Server-side video converter — capability detection and upload analysis",
    description=(
        "Phase 1 exposes health and FFmpeg/ffprobe capability detection. "
        "Phase 2 accepts a video upload and returns ffprobe metadata. "
        "Phase 3 converts an uploaded video to H.264 Main MP4 on the server. "
        "Encoder availability is reported only when the installed FFmpeg lists that encoder."
    ),
    lifespan=lifespan,
)

app.include_router(health_router, prefix="/api")
app.include_router(capabilities_router, prefix="/api")
app.include_router(jobs_router, prefix="/api")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' blob:; media-src 'self' blob:"
    return response


origins = [item.strip() for item in get_settings().cors_origins.split(",") if item.strip()]
if origins:
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["*"])

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND), name="frontend")


@app.get("/")
def root() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")
