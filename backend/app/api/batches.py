"""Batch conversion. Child jobs stay independent."""

import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.config import Settings
from app.core.rate_limit import limit
from app.core.security import is_job_id
from app.dependencies import settings_dependency
from app.schemas.jobs import ConvertRequest
from app.services.batches import BatchStore
from app.services.command_builder import CommandError, EncoderUnavailable
from app.services.converter import ConversionError, ConversionService
from app.services.queue import QueueUnavailable, get_queue
from app.services.storage import InsufficientDiskSpace, LocalStorage
from app.api.jobs import get_converter, get_storage

logger = logging.getLogger(__name__)
router = APIRouter(tags=["batches"])


def get_batches(storage: LocalStorage = Depends(get_storage)) -> BatchStore:
    return BatchStore(storage)


def require_batch_id(batch_id: str) -> str:
    if not is_job_id(batch_id):
        raise HTTPException(status_code=404, detail="Batch not found.")
    return batch_id


@router.post("/batches")
def create_batch(
    request: Request,
    body: dict,
    storage: LocalStorage = Depends(get_storage),
    batches: BatchStore = Depends(get_batches),
    converter: ConversionService = Depends(get_converter),
    settings: Settings = Depends(settings_dependency),
) -> dict:
    limit(request, "batch", settings.rate_limit_conversions)
    job_ids = body.get("job_ids")
    if not isinstance(job_ids, list) or not job_ids:
        raise HTTPException(status_code=422, detail="Select at least one uploaded video.")
    if len(job_ids) > settings.max_batch_files:
        raise HTTPException(status_code=422, detail=f"A batch can contain at most {settings.max_batch_files} videos.")
    if any(not is_job_id(job_id) for job_id in job_ids):
        raise HTTPException(status_code=404, detail="Job not found.")
    plan_body = {key: value for key, value in body.items() if key != "job_ids"}
    try:
        plan = ConvertRequest.model_validate(plan_body)
    except Exception:
        raise HTTPException(status_code=422, detail="Those conversion settings are not valid.") from None
    for job_id in job_ids:
        if storage.read_metadata(job_id) is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        try:
            converter.preview(job_id, plan)
        except (EncoderUnavailable, CommandError, ConversionError) as exc:
            raise HTTPException(status_code=422, detail=getattr(exc, "message", str(exc))) from None
    payload = batches.create(job_ids, plan.model_dump())
    queued = []
    try:
        for job_id in job_ids:
            converter.accept(job_id, plan)
            queued.append(job_id)
    except QueueUnavailable as exc:
        payload["status"] = "failed"
        payload["error"] = str(exc)
        payload["queued_job_ids"] = queued
        batches.write(payload["batch_id"], payload)
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except InsufficientDiskSpace as exc:
        payload["error"] = str(exc)
        batches.write(payload["batch_id"], payload)
        raise HTTPException(status_code=507, detail=str(exc)) from None
    except ConversionError as exc:
        payload["error"] = exc.message
        batches.write(payload["batch_id"], payload)
        raise HTTPException(status_code=409, detail=exc.message) from None
    return _summary(storage, settings, payload)


@router.get("/batches/{batch_id}")
def get_batch(
    batch_id: str = Depends(require_batch_id),
    storage: LocalStorage = Depends(get_storage),
    batches: BatchStore = Depends(get_batches),
    settings: Settings = Depends(settings_dependency),
) -> dict:
    payload = batches.read(batch_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Batch not found.")
    return _summary(storage, settings, payload)


@router.post("/batches/{batch_id}/cancel")
def cancel_batch(
    batch_id: str = Depends(require_batch_id),
    storage: LocalStorage = Depends(get_storage),
    batches: BatchStore = Depends(get_batches),
    settings: Settings = Depends(settings_dependency),
) -> dict:
    payload = batches.read(batch_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Batch not found.")
    queue = get_queue(settings)
    for job_id in payload.get("job_ids") or []:
        job = storage.read_metadata(job_id) or {}
        status = job.get("status")
        if status == "queued":
            queue.remove(job_id)
            queue.request_cancel(job_id)
            storage.update_metadata(job_id, {"status": "cancelled"})
        elif status == "processing":
            queue.request_cancel(job_id)
    return _summary(storage, settings, payload)


@router.get("/batches/{batch_id}/events")
def batch_events(
    batch_id: str = Depends(require_batch_id),
    storage: LocalStorage = Depends(get_storage),
    batches: BatchStore = Depends(get_batches),
    settings: Settings = Depends(settings_dependency),
):
    if batches.read(batch_id) is None:
        raise HTTPException(status_code=404, detail="Batch not found.")

    def stream():
        seen = None
        for _ in range(120):
            payload = batches.read(batch_id)
            if payload is None:
                return
            summary = _summary(storage, settings, payload)
            encoded = json.dumps(summary)
            if encoded != seen:
                yield f"event: progress\ndata: {encoded}\n\n"
                seen = encoded
            if summary["status"] in {"completed", "completed_with_errors", "failed", "cancelled"}:
                yield f"event: {summary['status']}\ndata: {encoded}\n\n"
                return
            time.sleep(0.2)

    return StreamingResponse(stream(), media_type="text/event-stream")


def _summary(storage: LocalStorage, settings: Settings, payload: dict) -> dict:
    jobs = []
    counts = {"queued": 0, "processing": 0, "completed": 0, "failed": 0, "cancelled": 0}
    progress_total = 0.0
    for job_id in payload.get("job_ids") or []:
        job = storage.read_metadata(job_id) or {"job_id": job_id, "status": "failed"}
        status = job.get("status") or "failed"
        if status in counts:
            counts[status] += 1
        progress = get_queue(settings).read_progress(job_id) or {}
        percent = 100.0 if status == "completed" else float(progress.get("percent") or 0)
        progress_total += percent
        jobs.append({
            "job_id": job_id,
            "status": status,
            "filename": (job.get("file") or {}).get("filename"),
            "size_bytes": (job.get("file") or {}).get("size_bytes"),
            "percent": percent,
            "download_ready": status == "completed",
        })
    total = len(payload.get("job_ids") or [])
    return {
        "batch_id": payload["batch_id"],
        "status": _batch_status(counts, total),
        "total": total,
        "queued": counts["queued"],
        "processing": counts["processing"],
        "completed": counts["completed"],
        "failed": counts["failed"],
        "cancelled": counts["cancelled"],
        "progress": round(progress_total / total, 1) if total else 0,
        "jobs": jobs,
        "error": payload.get("error"),
    }


def _batch_status(counts: dict, total: int) -> str:
    active = counts["queued"] + counts["processing"]
    if active and counts["processing"]:
        return "processing"
    if active:
        return "queued"
    if total and counts["completed"] == total:
        return "completed"
    if total and counts["cancelled"] == total:
        return "cancelled"
    if counts["completed"] and (counts["failed"] or counts["cancelled"]):
        return "completed_with_errors"
    if counts["failed"]:
        return "failed"
    return "failed"
