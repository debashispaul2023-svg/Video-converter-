"""Upload, status, conversion queue, progress, and download."""

import json
import logging
import time

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.config import Settings
from app.core.rate_limit import limit
from app.core.security import is_allowed_extension, is_job_id
from app.dependencies import capabilities_dependency, settings_dependency
from app.schemas.capabilities import CapabilitiesResponse
from app.schemas.conversion import ConversionPreview
from app.schemas.jobs import ConvertAccepted, ConvertRequest, JobResponse, OutputInfo, ProgressInfo, UploadedFileInfo
from app.schemas.media import MediaInfo
from app.services.command_builder import CommandError, EncoderUnavailable
from app.services.converter import ConversionError, ConversionService
from app.services.ffprobe import FFProbeService, InvalidMedia, ProbeTimeout, ProbeUnavailable
from app.services.queue import QueueUnavailable, get_queue
from app.services.storage import (
    InsufficientDiskSpace,
    LocalStorage,
    StorageError,
    UploadTimeout,
    UploadTooLarge,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["jobs"])


def get_storage(settings: Settings = Depends(settings_dependency)) -> LocalStorage:
    return LocalStorage(settings)


def get_probe(settings: Settings = Depends(settings_dependency)) -> FFProbeService:
    return FFProbeService(settings)


def get_converter(
    settings: Settings = Depends(settings_dependency),
    storage: LocalStorage = Depends(get_storage),
    probe: FFProbeService = Depends(get_probe),
    capabilities: CapabilitiesResponse = Depends(capabilities_dependency),
) -> ConversionService:
    return ConversionService(settings, storage, probe, capabilities)


def require_job_id(job_id: str) -> str:
    if not is_job_id(job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    return job_id


@router.post("/jobs", response_model=JobResponse)
async def create_job(
    request: Request,
    file: UploadFile = File(...),
    storage: LocalStorage = Depends(get_storage),
    probe: FFProbeService = Depends(get_probe),
    settings: Settings = Depends(settings_dependency),
) -> JobResponse:
    limit(request, "upload", settings.rate_limit_uploads)
    if not file.filename or not is_allowed_extension(file.filename):
        raise HTTPException(status_code=422, detail="This file type is not supported.")
    declared = _declared_length(file)
    if declared is not None and declared > storage.max_bytes:
        raise HTTPException(status_code=413, detail=UploadTooLarge(storage.max_bytes).args[0])
    try:
        if declared is not None:
            storage.ensure_space_for(declared)
        location = storage.create_job(file.filename)
    except InsufficientDiskSpace as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from None
    except StorageError:
        logger.exception("rejected unsafe job path")
        raise HTTPException(status_code=500, detail="Upload could not be stored.") from None
    try:
        size_bytes = await storage.save_upload(file, location)
        if size_bytes <= 0:
            raise HTTPException(status_code=422, detail="Uploaded file is empty.")
        media, _raw_probe = probe.analyze(str(location.input_path), location.display_name, size_bytes)
        storage.write_metadata(
            location,
            {
                "job_id": location.job_id,
                "status": "uploaded",
                "file": {"filename": location.display_name, "size_bytes": size_bytes},
                "media": media.model_dump(),
            },
        )
    except UploadTooLarge as exc:
        storage.cleanup(location.job_id)
        raise HTTPException(status_code=413, detail=str(exc)) from None
    except InsufficientDiskSpace as exc:
        storage.cleanup(location.job_id)
        raise HTTPException(status_code=507, detail=str(exc)) from None
    except UploadTimeout:
        storage.cleanup(location.job_id)
        raise HTTPException(status_code=408, detail="Upload timed out.") from None
    except ProbeTimeout:
        storage.cleanup(location.job_id)
        raise HTTPException(status_code=408, detail="Media analysis timed out.") from None
    except ProbeUnavailable:
        storage.cleanup(location.job_id)
        raise HTTPException(status_code=503, detail="Media analysis service is unavailable.") from None
    except InvalidMedia as exc:
        storage.cleanup(location.job_id)
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except HTTPException:
        storage.cleanup(location.job_id)
        raise
    except Exception:
        logger.exception("upload failed job_id=%s", location.job_id)
        storage.cleanup(location.job_id)
        raise HTTPException(status_code=500, detail="Upload could not be stored.") from None
    return _job_response(storage.read_metadata(location.job_id) or {})


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(
    job_id: str = Depends(require_job_id),
    storage: LocalStorage = Depends(get_storage),
    settings: Settings = Depends(settings_dependency),
) -> JobResponse:
    payload = storage.read_metadata(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return _job_response(payload, get_queue(settings).read_progress(job_id))


@router.post("/jobs/{job_id}/convert/validate", response_model=ConversionPreview)
def validate_conversion(
    request: Request,
    body: ConvertRequest,
    job_id: str = Depends(require_job_id),
    converter: ConversionService = Depends(get_converter),
    settings: Settings = Depends(settings_dependency),
) -> ConversionPreview:
    limit(request, "validate", settings.rate_limit_conversions)
    try:
        return ConversionPreview.model_validate(converter.preview(job_id, body))
    except EncoderUnavailable as exc:
        raise HTTPException(status_code=422, detail=exc.message) from None
    except CommandError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from None
    except ConversionError as exc:
        status_code = 404 if exc.message == "Job not found." else 422
        raise HTTPException(status_code=status_code, detail=exc.message) from None


@router.post("/jobs/{job_id}/convert", response_model=ConvertAccepted)
def convert_job(
    request: Request,
    body: ConvertRequest,
    job_id: str = Depends(require_job_id),
    converter: ConversionService = Depends(get_converter),
    settings: Settings = Depends(settings_dependency),
) -> ConvertAccepted:
    limit(request, "convert", settings.rate_limit_conversions)
    try:
        estimate = converter.accept(job_id, body)
    except EncoderUnavailable as exc:
        raise HTTPException(status_code=422, detail=exc.message) from None
    except CommandError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from None
    except InsufficientDiskSpace as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from None
    except QueueUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except ConversionError as exc:
        if exc.message == "Job not found.":
            status_code = 404
        elif "already" in exc.message:
            status_code = 409
        else:
            status_code = 422
        raise HTTPException(status_code=status_code, detail=exc.message) from None
    return ConvertAccepted(job_id=job_id, status="queued", estimate=estimate)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    request: Request,
    job_id: str = Depends(require_job_id),
    storage: LocalStorage = Depends(get_storage),
    settings: Settings = Depends(settings_dependency),
) -> dict:
    limit(request, "cancel", settings.rate_limit_conversions)
    payload = storage.read_metadata(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    status = payload.get("status")
    if status == "queued":
        get_queue(settings).remove(job_id)
        get_queue(settings).request_cancel(job_id)
        storage.update_metadata(job_id, {"status": "cancelled"})
        return {"job_id": job_id, "status": "cancelled"}
    if status == "processing":
        get_queue(settings).request_cancel(job_id)
        return {"job_id": job_id, "status": "processing"}
    raise HTTPException(status_code=409, detail="This job cannot be cancelled.")


@router.get("/jobs/{job_id}/events")
def job_events(
    job_id: str = Depends(require_job_id),
    storage: LocalStorage = Depends(get_storage),
    settings: Settings = Depends(settings_dependency),
):
    if storage.read_metadata(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    def stream():
        seen = None
        for _ in range(120):
            payload = storage.read_metadata(job_id) or {}
            status = payload.get("status")
            progress = get_queue(settings).read_progress(job_id) or {}
            event = "progress"
            if status == "completed":
                event = "completed"
            elif status == "failed":
                event = "failed"
            elif status == "cancelled":
                event = "cancelled"
            elif status == "expired":
                event = "expired"
            data = {"job_id": job_id, "status": status, "percent": progress.get("percent"), "speed": progress.get("speed"), "eta_seconds": progress.get("eta_seconds")}
            if status == "failed":
                data["message"] = "Conversion failed."
            encoded = json.dumps(data)
            if encoded != seen:
                yield f"event: {event}\ndata: {encoded}\n\n"
                seen = encoded
            if status in {"completed", "failed", "cancelled", "expired"}:
                return
            time.sleep(0.2)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/jobs/{job_id}/download")
def download_job(job_id: str = Depends(require_job_id), storage: LocalStorage = Depends(get_storage)) -> FileResponse:
    payload = storage.read_metadata(job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if payload.get("status") != "completed":
        raise HTTPException(status_code=409, detail="Output is not ready.")
    output = storage.output_file(job_id)
    if output is None or not output.is_file() or output.stat().st_size <= 0:
        raise HTTPException(status_code=409, detail="Output is not ready.")
    return FileResponse(output, media_type="video/mp4", filename="output.mp4", content_disposition_type="attachment")


def _job_response(payload: dict, progress: dict | None = None) -> JobResponse:
    output = payload.get("output")
    return JobResponse(
        job_id=payload["job_id"],
        status=payload.get("status") or "failed",
        file=UploadedFileInfo.model_validate(payload["file"]) if payload.get("file") else None,
        media=MediaInfo.model_validate(payload["media"]) if payload.get("media") else None,
        output=OutputInfo.model_validate(output) if output else None,
        error=payload.get("error"),
        estimate=payload.get("estimate"),
        progress=ProgressInfo.model_validate(progress) if progress and progress.get("percent") is not None else None,
    )


def _declared_length(upload: UploadFile) -> int | None:
    headers = getattr(upload, "headers", None)
    if headers is None:
        return None
    raw = headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
