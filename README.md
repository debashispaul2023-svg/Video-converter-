# VideoForge

A server-side video converter and compressor.

Video conversion runs on the server. The browser does not perform video encoding.

```
Browser/Phone
    -> FastAPI
    -> Upload
    -> ffprobe
    -> Redis
    -> Worker
    -> FFmpeg
    -> Output
    -> Download
```

The phone selects a file, uploads it, sends structured settings, and downloads the result. It does not run FFmpeg.

## Features

- Streamed uploads up to 20 GiB, with a 5 GiB free-space reserve
- ffprobe metadata extraction
- H.264 Baseline, Main, High, and High 10 when the installed FFmpeg supports them
- H.265/HEVC when `libx265` is installed
- Optional AV1, VP8, VP9, and MPEG formats only when their encoders and matching audio codecs are installed
- AAC stereo is the default audio codec
- CRF, bitrate, and target-size controls
- Resolution, frame rate, and H.264 level checks
- Redis-backed queue, worker progress, cancellation, and download
- Job isolation, path checks, and rate limits

Availability depends on the installed FFmpeg build. A discovered encoder is not automatically a conversion preset.

## H.264 Main

The default preset is `h264_main`: MP4, libx264, Main profile, yuv420p, AAC stereo, CRF 23, medium preset, and faststart. H.264 levels are settings on that codec, not separate codecs.

## Local development

Prerequisites: Python 3.10+, FFmpeg, ffprobe, and Redis.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env
PYTHONPATH=backend uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000
PYTHONPATH=backend python -m app.workers.conversion_worker
```

Open `http://localhost:8000`. The API and worker are separate processes. Tests use an in-process Redis stand-in:

```bash
REDIS_URL=fakeredis:// PYTHONPATH=backend pytest -q
```

## Docker

```bash
docker compose up --build
```

Compose starts Redis, the API on port 8000, and the worker. They share a job volume. The image includes FFmpeg and ffprobe and runs as a non-root user. No secrets are baked into the image.

## API

- `GET /api/health` process liveness
- `GET /api/ready` FFmpeg, ffprobe, Redis, and storage
- `GET /api/capabilities` detected encoders, presets, and explicit formats
- `POST /api/jobs` streamed upload
- `POST /api/jobs/{id}/convert/validate` settings check, no FFmpeg
- `POST /api/jobs/{id}/convert` queue a preset
- `GET /api/jobs/{id}` status and progress
- `GET /api/jobs/{id}/events` Server-Sent Events
- `POST /api/jobs/{id}/cancel` cancel that job only
- `GET /api/jobs/{id}/download` completed output only

## Production notes

Set `REDIS_URL`, `TEMP_DIRECTORY`, and `CORS_ORIGINS` in the environment. CORS is same-origin unless origins are listed. Uploads stop at `MAX_UPLOAD_SIZE_BYTES`. Conversion timeout is `CONVERSION_TIMEOUT_SECONDS`. Finished jobs expire after `JOB_RETENTION_SECONDS`. A reverse proxy must allow large request bodies and must not buffer the event stream. On worker start, interrupted processing jobs are marked failed and partial output is removed.

## License

LICENSE decision required.
