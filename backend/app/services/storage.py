"""Isolated temporary storage. Uploads stream to the job file and are not copied."""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from app.config import Settings
from app.core.security import extension_for, sanitize_filename

logger = logging.getLogger(__name__)

_JOB_ID_RE_LENGTH = 36
_GIB = 1024 ** 3


class StorageError(Exception):
    """Raised when a path would leave the configured temp root."""


class UploadTooLarge(Exception):
    def __init__(self, limit_bytes: int) -> None:
        self.limit_bytes = limit_bytes
        super().__init__(
            f"Upload exceeds the server's maximum supported file size of {format_size(limit_bytes)}."
        )


class InsufficientDiskSpace(Exception):
    def __init__(self) -> None:
        super().__init__("The server does not have enough free storage for this upload.")


class UploadTimeout(Exception):
    def __init__(self) -> None:
        super().__init__("Upload timed out.")


@dataclass(frozen=True)
class JobLocation:
    job_id: str
    upload_id: str
    directory: Path
    input_path: Path
    metadata_path: Path
    upload_state_path: Path
    display_name: str


class LocalStorage:
    _write_lock = threading.Lock()

    def __init__(self, settings: Settings) -> None:
        self.root = Path(settings.temp_directory).resolve()
        self.max_bytes = settings.max_upload_size_bytes
        self.min_free_bytes = settings.min_free_disk_space_bytes
        self.chunk_bytes = max(64 * 1024, settings.upload_chunk_bytes)
        self.upload_timeout_seconds = settings.upload_timeout_seconds

    def create_job(self, original_filename: str | None) -> JobLocation:
        self.ensure_space_for(None)
        job_id = str(uuid.uuid4())
        upload_id = str(uuid.uuid4())
        directory = self._contained(self.root / job_id)
        directory.mkdir(parents=True, exist_ok=False)
        display_name = sanitize_filename(original_filename)
        extension = extension_for(display_name) or ".mp4"
        location = JobLocation(
            job_id=job_id,
            upload_id=upload_id,
            directory=directory,
            input_path=self._contained(directory / f"input{extension}"),
            metadata_path=self._contained(directory / "metadata.json"),
            upload_state_path=self._contained(directory / "upload.json"),
            display_name=display_name,
        )
        self.write_upload_state(location, uploaded_bytes=0, chunk_number=0, total_bytes=None, complete=False)
        logger.info("upload start job_id=%s upload_id=%s", job_id, upload_id)
        return location

    def ensure_space_for(self, incoming_bytes: int | None) -> None:
        """Reject work that would leave less than the configured free reserve."""

        free = self.free_bytes()
        if free < self.min_free_bytes:
            raise InsufficientDiskSpace()
        if incoming_bytes is not None and free < incoming_bytes + self.min_free_bytes:
            raise InsufficientDiskSpace()

    def free_bytes(self) -> int:
        self.root.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(self.root).free

    async def save_upload(self, upload: UploadFile, location: JobLocation) -> int:
        """Stream the request body to input.ext. Chunks are not accumulated."""

        written = 0
        chunk_number = 0
        started = time.monotonic()
        try:
            with location.input_path.open("wb") as handle:
                while True:
                    if time.monotonic() - started > self.upload_timeout_seconds:
                        raise UploadTimeout()
                    chunk = await upload.read(self.chunk_bytes)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > self.max_bytes:
                        raise UploadTooLarge(self.max_bytes)
                    self.ensure_space_for(len(chunk))
                    handle.write(chunk)
                    chunk_number += 1
                    if chunk_number == 1 or chunk_number % 32 == 0:
                        self.write_upload_state(
                            location,
                            uploaded_bytes=written,
                            chunk_number=chunk_number,
                            total_bytes=None,
                            complete=False,
                        )
        except Exception:
            self._remove_file(location.input_path)
            raise
        self.write_upload_state(
            location,
            uploaded_bytes=written,
            chunk_number=chunk_number,
            total_bytes=written,
            complete=True,
        )
        logger.info(
            "upload complete job_id=%s size_bytes=%s chunks=%s",
            location.job_id,
            written,
            chunk_number,
        )
        return written

    def write_upload_state(
        self,
        location: JobLocation,
        *,
        uploaded_bytes: int,
        chunk_number: int,
        total_bytes: int | None,
        complete: bool,
    ) -> None:
        payload = {
            "job_id": location.job_id,
            "upload_id": location.upload_id,
            "chunk_size": self.chunk_bytes,
            "chunk_number": chunk_number,
            "uploaded_bytes": uploaded_bytes,
            "total_bytes": total_bytes,
            "complete": complete,
        }
        temporary = location.upload_state_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(location.upload_state_path)

    def write_metadata(self, location: JobLocation, payload: dict) -> None:
        temporary = location.metadata_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(location.metadata_path)

    def read_metadata(self, job_id: str) -> dict | None:
        if not _is_job_id(job_id):
            return None
        metadata_path = self._job_directory(job_id) / "metadata.json"
        try:
            metadata_path = self._contained(metadata_path)
        except StorageError:
            return None
        if not metadata_path.is_file():
            return None
        return json.loads(metadata_path.read_text(encoding="utf-8"))

    def update_metadata(self, job_id: str, changes: dict) -> dict | None:
        with self._write_lock:
            payload = self.read_metadata(job_id)
            if payload is None:
                return None
            payload.update(changes)
            metadata_path = self._contained(self._job_directory(job_id) / "metadata.json")
            temporary = metadata_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            temporary.replace(metadata_path)
            return payload

    def input_file(self, job_id: str) -> Path | None:
        if not _is_job_id(job_id):
            return None
        directory = self._job_directory(job_id)
        try:
            directory = self._contained(directory)
        except StorageError:
            return None
        if not directory.is_dir():
            return None
        matches = sorted(path for path in directory.glob("input.*") if path.is_file())
        return matches[0] if matches else None

    def output_file(self, job_id: str) -> Path | None:
        if not _is_job_id(job_id):
            return None
        try:
            return self._contained(self._job_directory(job_id) / "output.mp4")
        except StorageError:
            return None

    def cleanup(self, job_id: str) -> None:
        if not _is_job_id(job_id):
            return
        directory = self._job_directory(job_id)
        try:
            directory = self._contained(directory)
        except StorageError:
            logger.error("cleanup rejected job_id=%s", job_id)
            return
        if not directory.exists():
            return
        try:
            shutil.rmtree(directory)
        except OSError:
            logger.exception("cleanup failure job_id=%s", job_id)

    def _job_directory(self, job_id: str) -> Path:
        return self.root / job_id

    def _contained(self, path: Path) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        resolved = path.resolve()
        root = self.root.resolve()
        if resolved != root and root not in resolved.parents:
            raise StorageError("path escaped temp directory")
        return resolved

    @staticmethod
    def _remove_file(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("failed to remove partial upload")


def format_size(size_bytes: int) -> str:
    if size_bytes % _GIB == 0:
        return f"{size_bytes // _GIB} GB"
    return f"{size_bytes} bytes"


def _is_job_id(job_id: str) -> bool:
    if len(job_id) != _JOB_ID_RE_LENGTH:
        return False
    try:
        parsed = uuid.UUID(job_id)
    except ValueError:
        return False
    return parsed.version == 4 and str(parsed) == job_id
