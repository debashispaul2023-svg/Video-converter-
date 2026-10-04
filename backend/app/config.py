"""Application settings. Secrets and limits come from the environment."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    Phase 1 uses the FFmpeg binary paths and probe timeout. Phase 2 uses the
    upload limit, temp directory, and ffprobe timeout. Remaining variables are
    reserved for later phases.
    """

    ffmpeg_binary: str = Field(default="ffmpeg", alias="FFMPEG_BINARY")
    ffprobe_binary: str = Field(default="ffprobe", alias="FFPROBE_BINARY")
    capability_probe_timeout_seconds: int = Field(
        default=20, alias="CAPABILITY_PROBE_TIMEOUT_SECONDS"
    )
    ffprobe_timeout_seconds: int = Field(default=30, alias="FFPROBE_TIMEOUT_SECONDS")
    upload_timeout_seconds: int = Field(default=21600, alias="UPLOAD_TIMEOUT_SECONDS")
    conversion_timeout_seconds: int = Field(default=43200, alias="CONVERSION_TIMEOUT_SECONDS")
    default_aac_bitrate: str = Field(default="128k", alias="DEFAULT_AAC_BITRATE")
    default_h264_crf: int = Field(default=23, alias="DEFAULT_H264_CRF")
    default_x264_preset: str = Field(default="medium", alias="DEFAULT_X264_PRESET")
    default_h265_crf: int = Field(default=28, alias="DEFAULT_H265_CRF")
    default_h265_preset: str = Field(default="medium", alias="DEFAULT_H265_PRESET")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Canonical limits are bytes. 20 GiB and 5 GiB; documented as GB in errors.
    max_upload_size_bytes: int = Field(default=21474836480, alias="MAX_UPLOAD_SIZE_BYTES")
    min_free_disk_space_bytes: int = Field(default=5368709120, alias="MIN_FREE_DISK_SPACE_BYTES")
    upload_chunk_bytes: int = Field(default=1048576, alias="UPLOAD_CHUNK_BYTES")
    max_concurrent_jobs: int = Field(default=1, alias="MAX_CONCURRENT_JOBS")
    job_timeout_seconds: int = Field(default=3600, alias="JOB_TIMEOUT_SECONDS")
    job_retention_minutes: int = Field(default=60, alias="JOB_RETENTION_MINUTES")
    job_retention_seconds: int = Field(default=86400, alias="JOB_RETENTION_SECONDS")
    worker_concurrency: int = Field(default=1, alias="WORKER_CONCURRENCY")
    temp_directory: str = Field(default="/tmp/video-converter", alias="TEMP_DIRECTORY")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    cors_origins: str = Field(default="", alias="CORS_ORIGINS")
    rate_limit_uploads: int = Field(default=30, alias="RATE_LIMIT_UPLOADS")
    rate_limit_conversions: int = Field(default=60, alias="RATE_LIMIT_CONVERSIONS")
    max_target_size_bytes: int = Field(default=21474836480, alias="MAX_TARGET_SIZE_BYTES")
    max_batch_files: int = Field(default=30, alias="MAX_BATCH_FILES")
    min_batch_files: int = Field(default=20, alias="MIN_BATCH_FILES")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator(
        "max_upload_size_bytes",
        "min_free_disk_space_bytes",
        "upload_timeout_seconds",
        "ffprobe_timeout_seconds",
        "conversion_timeout_seconds",
        "job_retention_seconds",
        "worker_concurrency",
        "max_target_size_bytes",
        "max_batch_files",
        "min_batch_files",
    )
    @classmethod
    def positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Configuration values must be positive.")
        return value

    @field_validator("redis_url")
    @classmethod
    def redis_url_scheme(cls, value: str) -> str:
        if not (value.startswith("redis://") or value.startswith("fakeredis://")):
            raise ValueError("REDIS_URL must use redis://.")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
