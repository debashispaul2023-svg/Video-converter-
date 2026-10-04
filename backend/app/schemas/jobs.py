"""Job schemas for upload, status, and conversion."""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.conversion import CompressionResult, ConvertRequest, SizeEstimate
from app.schemas.media import MediaInfo

JobStatus = Literal[
    "uploading",
    "uploaded",
    "queued",
    "processing",
    "completed",
    "failed",
    "cancelled",
    "expired",
]


class UploadedFileInfo(BaseModel):
    filename: str
    size_bytes: int


class OutputInfo(BaseModel):
    filename: str
    size_bytes: int
    compression: CompressionResult | None = None


class ProgressInfo(BaseModel):
    percent: float
    current_time_seconds: float | None = None
    duration_seconds: float | None = None
    speed: float | None = None
    eta_seconds: float | None = None


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    file: UploadedFileInfo | None = None
    media: MediaInfo | None = None
    output: OutputInfo | None = None
    error: str | None = None
    estimate: SizeEstimate | None = None
    progress: ProgressInfo | None = None


class ConvertAccepted(BaseModel):
    job_id: str
    status: Literal["queued"]
    estimate: SizeEstimate | None = None


__all__ = ["ConvertRequest", "JobResponse", "OutputInfo", "UploadedFileInfo"]
