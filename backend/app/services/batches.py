"""Batch metadata. Each child remains an independent conversion job."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from app.core.security import is_job_id
from app.services.storage import LocalStorage, StorageError


class BatchStore:
    def __init__(self, storage: LocalStorage) -> None:
        self.storage = storage
        self.root = storage.root / "batches"

    def create(self, job_ids: list[str], plan: dict) -> dict:
        batch_id = str(uuid.uuid4())
        directory = self._directory(batch_id)
        directory.mkdir(parents=True, exist_ok=False)
        payload = {
            "batch_id": batch_id,
            "job_ids": job_ids,
            "plan": plan,
            "status": "queued",
            "error": None,
        }
        self.write(batch_id, payload)
        return payload

    def write(self, batch_id: str, payload: dict) -> None:
        path = self._metadata(batch_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(path)

    def read(self, batch_id: str) -> dict | None:
        if not is_job_id(batch_id):
            return None
        path = self._metadata(batch_id)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _directory(self, batch_id: str) -> Path:
        if not is_job_id(batch_id):
            raise StorageError("invalid batch id")
        directory = (self.root / batch_id).resolve()
        root = self.root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        if directory != root and root not in directory.parents and directory != root / batch_id:
            raise StorageError("path escaped temp directory")
        return directory

    def _metadata(self, batch_id: str) -> Path:
        return self._directory(batch_id) / "batch.json"
