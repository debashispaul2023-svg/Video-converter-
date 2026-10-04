"""Filename and path checks. User input never becomes a filesystem path or command."""

from __future__ import annotations

import re

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_ALLOWED_EXTENSIONS = {
    ".mp4",
    ".m4v",
    ".mkv",
    ".mov",
    ".webm",
    ".avi",
    ".mpeg",
    ".mpg",
    ".ts",
    ".m2ts",
    ".mts",
    ".flv",
    ".3gp",
    ".3g2",
}


def sanitize_filename(raw_name: str | None) -> str:
    """Return a display-safe basename. Never includes a directory."""

    if not raw_name:
        return "upload.mp4"
    flattened = raw_name.replace("\\", "/").replace("\x00", "")
    basename = flattened.rsplit("/", 1)[-1].strip()
    cleaned = _SAFE_NAME_RE.sub("_", basename).strip("._")
    if not cleaned or cleaned in {".", ".."}:
        return "upload.mp4"
    return cleaned[:120]


_JOB_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def is_job_id(value: str) -> bool:
    return bool(_JOB_ID_RE.fullmatch(value or ""))


def extension_for(filename: str) -> str:
    sanitized = sanitize_filename(filename)
    dot = sanitized.rfind(".")
    if dot <= 0:
        return ""
    return sanitized[dot:].lower()


def is_allowed_extension(filename: str) -> bool:
    return extension_for(filename) in _ALLOWED_EXTENSIONS
