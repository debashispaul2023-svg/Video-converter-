"""Run the H.264 Main preset on a server-side input file.

The HTTP layer starts this work and returns. A later phase can move `_execute`
onto a queue without changing the argument builder.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

from app.config import Settings
from app.schemas.capabilities import CapabilitiesResponse
from app.schemas.conversion import ConvertRequest
from app.services.command_builder import EncoderUnavailable, build_preset_command
from app.services.ffprobe import FFProbeService, ProbeError
from app.services.presets import PRESETS
from app.services.progress import parse_progress
from app.services.quality import compression_result
from app.services.queue import JobQueue, QueueUnavailable, get_queue
from app.services.storage import InsufficientDiskSpace, LocalStorage

logger = logging.getLogger(__name__)


class ConversionError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class ConversionService:
    def __init__(
        self,
        settings: Settings,
        storage: LocalStorage,
        probe: FFProbeService,
        capabilities: CapabilitiesResponse,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.probe = probe
        self.capabilities = capabilities

    def accept(self, job_id: str, request: ConvertRequest | None = None) -> dict:
        body = request or ConvertRequest()
        payload = self.storage.read_metadata(job_id)
        if payload is None:
            raise ConversionError("Job not found.")
        status = payload.get("status")
        if status == "processing":
            raise ConversionError("This job is already processing.")
        if status == "completed":
            raise ConversionError("This job is already completed.")
        if status != "uploaded" and status != "failed":
            raise ConversionError("This job is not ready for conversion.")
        input_path = self.storage.input_file(job_id)
        if input_path is None or not input_path.is_file():
            raise ConversionError("Uploaded file is missing.")
        media = payload.get("media") or {}
        has_audio = bool((media.get("audio") or {}).get("codec"))
        output_path = self.storage.output_file(job_id)
        if output_path is None:
            raise ConversionError("Output path could not be created.")
        _arguments, plan = build_preset_command(
            self.settings,
            self.capabilities,
            body.preset,
            input_path,
            output_path,
            has_audio=has_audio,
            request=body,
            media=media,
            original_size_bytes=input_path.stat().st_size,
        )
        self.storage.ensure_space_for(input_path.stat().st_size)
        updated = self.storage.update_metadata(
            job_id,
            {
                "status": "queued",
                "error": None,
                "output": None,
                "preset": body.preset,
                "conversion": body.model_dump(),
                "estimate": plan.estimate.model_dump(),
            },
        )
        if updated is None:
            raise ConversionError("Job not found.")
        try:
            get_queue(self.settings).enqueue(job_id, self.settings.temp_directory)
        except QueueUnavailable:
            self.storage.update_metadata(job_id, {"status": "uploaded"})
            raise
        logger.info("state queued job_id=%s", job_id)
        return plan.estimate.model_dump()

    def preview(self, job_id: str, request: ConvertRequest) -> dict:
        payload = self.storage.read_metadata(job_id)
        if payload is None:
            raise ConversionError("Job not found.")
        input_path = self.storage.input_file(job_id)
        if input_path is None:
            raise ConversionError("Uploaded file is missing.")
        media = payload.get("media") or {}
        has_audio = bool((media.get("audio") or {}).get("codec"))
        output_path = self.storage.output_file(job_id)
        if output_path is None:
            raise ConversionError("Output path could not be created.")
        _arguments, plan = build_preset_command(
            self.settings,
            self.capabilities,
            request.preset,
            input_path,
            output_path,
            has_audio=has_audio,
            request=request,
            media=media,
            original_size_bytes=input_path.stat().st_size,
        )
        return {
            "valid": True,
            "preset": request.preset,
            "encoder": PRESETS[request.preset].encoder,
            "quality_mode": plan.quality_mode,
            "crf": plan.crf,
            "video_bitrate_bps": plan.video_bitrate_bps,
            "resolution": request.resolution,
            "width": plan.width,
            "height": plan.height,
            "fps": plan.fps,
            "audio_mode": plan.audio_mode,
            "audio_bitrate": plan.audio_bitrate,
            "estimate": plan.estimate.model_dump(),
            "errors": [],
        }

    def execute(self, job_id: str) -> None:
        payload = self.storage.read_metadata(job_id)
        if payload is None or payload.get("status") in {"cancelled", "completed", "failed"}:
            return
        if get_queue(self.settings).cancel_requested(job_id):
            self._cancel(job_id)
            return
        self.storage.update_metadata(job_id, {"status": "processing"})
        logger.info("state processing job_id=%s", job_id)
        try:
            self._convert(job_id, payload.get("preset") or "h264_main")
        except Exception:
            logger.exception("conversion failed job_id=%s", job_id)
            self._fail(job_id, "Conversion failed.")

    def _convert(self, job_id: str, preset_id: str) -> None:
        payload = self.storage.read_metadata(job_id) or {}
        input_path = self.storage.input_file(job_id)
        output_path = self.storage.output_file(job_id)
        if input_path is None or output_path is None:
            self._fail(job_id, "Uploaded file is missing.")
            return
        partial_path = output_path.with_name("output.partial.mp4")
        has_audio = bool(((payload.get("media") or {}).get("audio") or {}).get("codec"))
        stored = payload.get("conversion") or {"preset": preset_id}
        try:
            request = ConvertRequest.model_validate(stored)
            arguments, plan = build_preset_command(
                self.settings,
                self.capabilities,
                preset_id,
                input_path,
                partial_path,
                has_audio=has_audio,
                request=request,
                media=payload.get("media") or {},
                original_size_bytes=input_path.stat().st_size,
            )
        except EncoderUnavailable as exc:
            self._fail(job_id, exc.message)
            return
        except CommandError as exc:
            self._fail(job_id, exc.message)
            return
        binary = self._ffmpeg()
        if binary is None:
            self._fail(job_id, "Conversion service is unavailable.")
            return
        logger.info("conversion start job_id=%s preset=%s", job_id, preset_id)
        duration = ((payload.get("media") or {}).get("duration_seconds"))
        try:
            completed = _run_ffmpeg(
                [binary, *arguments],
                timeout=self.settings.conversion_timeout_seconds,
                duration=duration,
                on_progress=lambda progress: self._store_progress(job_id, progress),
                should_cancel=lambda: get_queue(self.settings).cancel_requested(job_id),
            )
        except subprocess.TimeoutExpired:
            logger.error("conversion timeout job_id=%s", job_id)
            self._remove_output(partial_path)
            self._remove_output(output_path)
            self._fail(job_id, "Conversion timed out.")
            return
        except Cancelled:
            self._remove_output(partial_path)
            self._remove_output(output_path)
            self._cancel(job_id)
            return
        except OSError:
            logger.exception("conversion failed to start job_id=%s", job_id)
            self._fail(job_id, "Conversion service is unavailable.")
            return
        if completed.returncode != 0:
            logger.error("conversion exit job_id=%s code=%s", job_id, completed.returncode)
            self._remove_output(partial_path)
            self._remove_output(output_path)
            self._fail(job_id, "Conversion failed.")
            return
        if not partial_path.is_file() or partial_path.stat().st_size <= 0:
            self._remove_output(partial_path)
            self._remove_output(output_path)
            self._fail(job_id, "Conversion failed.")
            return
        try:
            output_media, _raw = self.probe.analyze(
                str(partial_path), "output.mp4", partial_path.stat().st_size
            )
        except ProbeError:
            logger.error("output validation failed job_id=%s", job_id)
            self._remove_output(partial_path)
            self._remove_output(output_path)
            self._fail(job_id, "Output validation failed.")
            return
        expect_audio = plan.audio_mode != "none"
        if not _output_matches(output_media, preset_id, expect_audio=expect_audio):
            self._remove_output(partial_path)
            self._remove_output(output_path)
            self._fail(job_id, "Output validation failed.")
            return
        partial_path.replace(output_path)
        original_size = input_path.stat().st_size
        self.storage.update_metadata(
            job_id,
            {
                "status": "completed",
                "error": None,
                "output": {
                    "filename": "output.mp4",
                    "size_bytes": output_path.stat().st_size,
                    "media": output_media.model_dump(),
                    "compression": compression_result(original_size, output_path.stat().st_size),
                },
            },
        )
        logger.info("conversion complete job_id=%s size_bytes=%s", job_id, output_path.stat().st_size)

    def _store_progress(self, job_id: str, progress: dict) -> None:
        get_queue(self.settings).write_progress(job_id, progress)
        path = self.storage.output_file(job_id)
        if path is None:
            return
        progress_path = path.with_name("progress.json")
        now = time.monotonic()
        last = getattr(self, "_progress_written", 0)
        if now - last < 1:
            return
        self._progress_written = now
        progress_path.write_text(json.dumps(progress), encoding="utf-8")

    def _cancel(self, job_id: str) -> None:
        output = self.storage.output_file(job_id)
        if output is not None:
            self._remove_output(output)
            self._remove_output(output.with_name("output.partial.mp4"))
        self.storage.update_metadata(job_id, {"status": "cancelled", "error": None, "output": None})
        logger.info("state cancelled job_id=%s", job_id)

    def _ffmpeg(self) -> str | None:
        import shutil

        candidate = self.settings.ffmpeg_binary.strip()
        if not candidate or any(char in candidate for char in ("\n", "\r", "\x00")):
            return None
        return shutil.which(candidate)

    def _fail(self, job_id: str, message: str) -> None:
        self.storage.update_metadata(job_id, {"status": "failed", "error": message, "output": None})

    @staticmethod
    def _remove_output(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("failed to remove invalid output")


class Cancelled(Exception):
    pass


def _run_ffmpeg(command, timeout, duration, on_progress, should_cancel):
    progress_args = ["-progress", "pipe:1", "-nostats"]
    command = [command[0], *progress_args, *command[1:]]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    started = time.monotonic()
    block = []
    assert process.stdout is not None
    while True:
        if should_cancel():
            _stop_process(process)
            raise Cancelled()
        if time.monotonic() - started > timeout:
            _stop_process(process)
            raise subprocess.TimeoutExpired(command, timeout)
        line = process.stdout.readline()
        if line:
            block.append(line)
            if line.strip() == "progress=continue" or line.strip() == "progress=end":
                on_progress(parse_progress("".join(block), duration))
                block = []
        elif process.poll() is not None:
            break
        else:
            time.sleep(0.05)
    stderr = process.stderr.read() if process.stderr else ""
    return subprocess.CompletedProcess(command, process.returncode or 0, "", stderr)


def _stop_process(process: subprocess.Popen) -> None:
    import os
    import signal

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            process.kill()
        process.wait(timeout=5)


def _output_matches(media, preset_id: str, *, expect_audio: bool) -> bool:
    preset = PRESETS.get(preset_id)
    if preset is None:
        return False
    container = media.format_name or ""
    if "mp4" not in container.split(","):
        return False
    video = media.video
    if video is None or video.codec != preset.expected_codec:
        return False
    if (video.profile or "").lower() not in preset.accepted_profiles:
        return False
    if video.pixel_format != preset.pixel_format:
        return False
    if expect_audio:
        audio = media.audio
        if audio is None or audio.codec != "aac" or audio.channels != 2:
            return False
    return True
