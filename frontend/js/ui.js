export function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

export function formatDuration(seconds) {
  if (!Number.isFinite(seconds)) return "Unknown";
  const total = Math.round(seconds);
  const minutes = Math.floor(total / 60);
  return `${String(minutes).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

export function renderMedia(media, file) {
  const video = media.video || {};
  const audio = media.audio || {};
  return [
    ["File", file.filename],
    ["Size", formatBytes(file.size_bytes)],
    ["Duration", formatDuration(media.duration_seconds)],
    ["Video", `${video.width || "?"} \u00d7 ${video.height || "?"}`],
    ["FPS", video.fps ?? "Unknown"],
    ["Codec", video.codec || "Unknown"],
    ["Profile", video.profile || "Unknown"],
    ["Pixels", video.pixel_format || "Unknown"],
    ["Audio", audio.codec ? `${audio.codec}, ${audio.channels || "?"} ch` : "None"],
  ];
}

export function buildSettings(form) {
  const data = new FormData(form);
  const mode = data.get("mode");
  const body = {
    preset: data.get("preset") || "h264_main",
    quality_mode: mode,
    audio_mode: data.get("audio"),
    audio_bitrate: data.get("audio_bitrate"),
    resolution: data.get("resolution"),
    fps: data.get("fps"),
    encoder_preset: data.get("speed"),
    level: data.get("level") || "auto",
  };
  if (data.get("format_id")) body.format_id = data.get("format_id");
  if (mode === "crf") {
    const preset = data.get("quality_preset");
    const crf = Number(data.get("crf"));
    const presetCrf = { very_high: 18, high: 21, balanced: 23, smaller_file: 27, maximum_compression: 30 }[preset];
    if (crf === presetCrf) body.quality_preset = preset;
    else body.crf = crf;
  }
  if (mode === "bitrate") body.video_bitrate = data.get("bitrate");
  if (mode === "target_size") body.target_size_bytes = Math.round(Number(data.get("target")) * 1024 * 1024);
  return body;
}
