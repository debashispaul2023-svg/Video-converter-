"""Parse FFmpeg machine-readable progress. Human stderr is not the protocol."""

from __future__ import annotations


def parse_progress(block: str, duration_seconds: float | None) -> dict:
    fields = {}
    for line in block.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        fields[key.strip()] = value.strip()
    current = _current_seconds(fields)
    speed = _speed(fields.get("speed"))
    percent = 0.0
    if duration_seconds and duration_seconds > 0 and current is not None:
        percent = min(100.0, round(current / duration_seconds * 100, 1))
    if fields.get("progress") == "end":
        percent = 100.0
    eta = None
    if duration_seconds and current is not None and speed and speed > 0 and current < duration_seconds:
        eta = round((duration_seconds - current) / speed, 1)
    return {
        "percent": percent,
        "current_time_seconds": current,
        "duration_seconds": duration_seconds,
        "speed": speed,
        "eta_seconds": eta,
    }


def _current_seconds(fields: dict) -> float | None:
    if fields.get("out_time_ms") not in (None, "N/A", ""):
        return int(fields["out_time_ms"]) / 1_000_000
    if fields.get("out_time_us") not in (None, "N/A", ""):
        return int(fields["out_time_us"]) / 1_000_000
    stamp = fields.get("out_time")
    if not stamp or stamp == "N/A":
        return None
    hours, minutes, seconds = stamp.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _speed(value: str | None) -> float | None:
    if not value or value in {"N/A", "0"}:
        return None
    token = value[:-1] if value.endswith("x") else value
    try:
        parsed = float(token)
    except ValueError:
        return None
    return parsed if parsed > 0 else None
