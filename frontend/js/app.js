import { batchEvents, cancel, cancelBatch, convert, createBatch, downloadUrl, events, getCapabilities, getJob, upload, validate } from "./api.js";
import { buildSettings, formatBytes, formatDuration, renderMedia } from "./ui.js";

const PRESET_LABELS = {
  h264_baseline: "H.264 Baseline · MP4",
  h264_main: "H.264 Main · MP4 · AAC · Compatible",
  h264_high: "H.264 High · MP4",
  h264_high10: "H.264 High 10 · MP4",
  h265_hevc: "H.265 / HEVC · MP4",
};
const QUALITY = [
  ["very_high", "Very high"],
  ["high", "High"],
  ["balanced", "Balanced"],
  ["smaller_file", "Smaller file"],
  ["maximum_compression", "Maximum compression"],
];
const BITRATES = ["500k", "1M", "2M", "4M", "6M", "8M", "10M", "15M", "20M"];
const SPEEDS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"];
const AUDIO_RATES = ["64k", "96k", "128k", "160k", "192k", "256k", "320k"];
const state = { jobId: null, source: null, events: null, files: [], batchId: null };

const $ = (id) => document.getElementById(id);

document.addEventListener("DOMContentLoaded", init);

async function init() {
  const capabilities = await getCapabilities();
  renderPresets(capabilities.presets || {}, capabilities.formats || []);
  fillSelect($("level"), (capabilities.h264_levels || ["auto"]).map((level) => [level, level]), "auto");
  renderChoices("quality-presets", "quality_preset", QUALITY, "balanced");
  fillSelect($("bitrate"), BITRATES, "2M");
  fillSelect($("speed"), SPEEDS, "medium");
  fillSelect($("audio-bitrate"), AUDIO_RATES, "128k");
  fillSelect($("resolution"), [["original", "Original"]], "original");
  fillSelect($("fps"), [["original", "Original"]], "original");
  $("choose").addEventListener("click", () => $("file").click());
  $("file").addEventListener("change", () => selected($("file").files));
  const drop = $("drop");
  ["dragover", "dragleave", "drop"].forEach((name) => drop.addEventListener(name, onDrop));
  $("settings").addEventListener("submit", startConversion);
  $("settings").addEventListener("change", () => checkSettings());
  $("cancel").addEventListener("click", cancelConversion);
  const saved = sessionStorage.getItem("videoforge-job");
  if (saved) restore(saved);
}

function renderPresets(presets, formats) {
  const box = $("presets");
  box.replaceChildren();
  Object.entries(presets).forEach(([id, preset]) => {
    if (!preset.available) return;
    const label = document.createElement("label");
    label.innerHTML = `<input type="radio" name="preset" value="${id}" ${id === "h264_main" ? "checked" : ""}> ${PRESET_LABELS[id] || id}`;
    box.append(label);
  });
  formats.filter((item) => item.available).forEach((item) => {
    const label = document.createElement("label");
    label.innerHTML = `<input type="radio" name="format_id" value="${item.id}"> ${item.name}`;
    box.append(label);
  });
}

function renderChoices(id, name, choices, checked) {
  const box = $(id);
  box.replaceChildren();
  choices.forEach(([value, label]) => {
    const row = document.createElement("label");
    row.innerHTML = `<input type="radio" name="${name}" value="${value}" ${value === checked ? "checked" : ""}> ${label}`;
    box.append(row);
  });
}

function fillSelect(select, values, selected) {
  select.replaceChildren();
  values.forEach((value) => {
    const option = document.createElement("option");
    const pair = Array.isArray(value) ? value : [value, value];
    option.value = pair[0];
    option.textContent = pair[1];
    option.selected = pair[0] === selected;
    select.append(option);
  });
}

function onDrop(event) {
  event.preventDefault();
  $("picker").classList.toggle("drag", event.type === "dragover");
  if (event.type === "drop") selected(event.dataTransfer.files);
}

async function selected(fileList) {
  const files = Array.from(fileList || []).slice(0, 30);
  state.files = files;
  const list = $("file-list");
  list.replaceChildren();
  $("selected-count").textContent = `Selected: ${files.length} / 30`;
  files.forEach((file, index) => {
    const item = document.createElement("li");
    item.textContent = `${file.name} · ${formatBytes(file.size)}`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      state.files.splice(index, 1);
      selected(state.files);
    });
    item.append(remove);
    list.append(item);
  });
  const convert = $("convert");
  if (files.length > 30) {
    convert.disabled = true;
    setMessage("Maximum 30 videos allowed.", "error");
    return;
  }
  if (files.length === 1) {
    convert.disabled = false;
    show("upload-panel");
    $("file-name").textContent = files[0].name;
    $("file-size").textContent = formatBytes(files[0].size);
    const started = performance.now();
    try {
      const job = await upload(files[0], (loaded, total) => showUpload(loaded, total, started));
      state.jobId = job.job_id;
      state.source = job;
      sessionStorage.setItem("videoforge-job", job.job_id);
      showMedia(job);
      setMessage("Upload complete.", "ok");
    } catch (error) {
      setMessage(error.message, "error");
    }
    return;
  }
  if (files.length > 1 && files.length < 20) {
    convert.disabled = true;
    show("workspace");
    setMessage("Select at least 20 videos to start a batch.", "error");
    return;
  }
  convert.disabled = files.length < 20;
  if (files.length >= 20) {
    show("workspace");
    setMessage("Batch ready", "ok");
  }
}

function showUpload(loaded, total, started) {
  const percent = Math.min(100, Math.round(loaded / total * 100));
  $("upload-bar").style.width = `${percent}%`;
  const seconds = (performance.now() - started) / 1000;
  const speed = seconds > 0 ? loaded / seconds : 0;
  const remaining = speed > 0 ? Math.max(0, total - loaded) / speed : null;
  $("upload-detail").textContent = `${percent}% · ${formatBytes(loaded)} / ${formatBytes(total)} · ${formatBytes(speed)}/s · ETA ${remaining == null ? "unknown" : `${Math.round(remaining)} sec`}`;
}

function showMedia(job) {
  show("workspace");
  const list = $("media");
  list.replaceChildren();
  renderMedia(job.media, job.file).forEach(([name, value]) => {
    const term = document.createElement("dt");
    term.textContent = name;
    const detail = document.createElement("dd");
    detail.textContent = value;
    list.append(term, detail);
  });
  const height = job.media.video?.height || 0;
  const fps = job.media.video?.fps || 0;
  const heights = [["original", "Original"], ["2160p", "2160p"], ["1440p", "1440p"], ["1080p", "1080p"], ["720p", "720p"], ["480p", "480p"], ["360p", "360p"]];
  fillSelect($("resolution"), heights.filter((item) => item[0] === "original" || Number(item[0]) <= height), "original");
  const rates = [["original", "Original"], ["24", "24"], ["25", "25"], ["30", "30"], ["50", "50"], ["60", "60"]];
  fillSelect($("fps"), rates.filter((item) => item[0] === "original" || Number(item[0]) <= fps + 0.01), "original");
}

async function checkSettings() {
  if (!state.jobId) return;
  try {
    const preview = await validate(state.jobId, buildSettings($("settings")));
    $("validation").textContent = preview.estimate?.note || "Settings accepted.";
    $("validation").className = "status ok";
    $("estimate").textContent = preview.estimate?.estimated_output_size_bytes
      ? `Estimated output: about ${formatBytes(preview.estimate.estimated_output_size_bytes)}. This is an estimate.`
      : "Output size is an estimate, not a guarantee.";
  } catch (error) {
    $("validation").textContent = error.message;
    $("validation").className = "status error";
  }
}

async function startConversion(event) {
  event.preventDefault();
  if (state.files.length > 1) {
    if (state.files.length < 20 || state.files.length > 30) return;
    return startBatch();
  }
  try {
    await validate(state.jobId, buildSettings($("settings")));
    const accepted = await convert(state.jobId, buildSettings($("settings")));
    showQueued(accepted.status);
    listen();
  } catch (error) {
    $("validation").textContent = error.message;
    $("validation").className = "status error";
  }
}

async function startBatch() {
  const jobIds = [];
  for (const file of state.files) {
    const job = await upload(file, () => {});
    jobIds.push(job.job_id);
  }
  const batch = await createBatch(jobIds, buildSettings($("settings")));
  state.batchId = batch.batch_id;
  show("progress-panel");
  state.events = batchEvents(batch.batch_id, (_type, data) => renderBatch(data));
}

function renderBatch(data) {
  $("progress-title").textContent = data.status;
  $("convert-bar").style.width = `${data.progress || 0}%`;
  $("progress-detail").textContent = `${data.completed || 0} / ${data.total || 0} completed`;
  const stats = $("result-stats");
  stats.replaceChildren();
  (data.jobs || []).forEach((job) => {
    const row = document.createElement("div");
    row.className = "batch-row";
    row.textContent = `${job.filename || job.job_id} · ${job.status} · ${job.percent || 0}%`;
    if (job.download_ready) {
      const link = document.createElement("a");
      link.href = downloadUrl(job.job_id);
      link.textContent = "Download";
      row.append(link);
    }
    stats.append(row);
  });
  show("result");
}

function showQueued(status) {
  show("progress-panel");
  $("cancel").classList.remove("hidden");
  $("progress-title").textContent = status === "processing" ? "Converting" : "Queued";
  $("progress-copy").textContent = status === "processing"
    ? "The server worker is encoding the video."
    : "Your video is waiting for the server worker.";
}

function listen() {
  state.events?.close();
  state.events = events(state.jobId, (type, data) => {
    if (type === "progress" || data.status === "processing") showProgress(data);
    if (type === "completed" || data.status === "completed") finish();
    if (type === "failed") fail(data.message);
    if (type === "cancelled") cancelled();
  });
}

function showProgress(data) {
  showQueued("processing");
  const percent = Number(data.percent || 0);
  $("convert-bar").style.width = `${Math.min(100, percent)}%`;
  $("progress-detail").textContent = `${percent}% · ${formatDuration(data.current_time_seconds)} / ${formatDuration(data.duration_seconds)} · Speed ${data.speed || "unknown"}x · ETA ${data.eta_seconds ?? "unknown"} sec`;
}

async function finish() {
  state.events?.close();
  const job = await getJob(state.jobId);
  hide("progress-panel");
  $("cancel").classList.add("hidden");
  show("result");
  const stats = $("result-stats");
  stats.replaceChildren();
  const compression = job.output?.compression;
  const rows = compression ? [
    ["Original", formatBytes(compression.original_size_bytes)],
    ["Output", formatBytes(compression.output_size_bytes)],
    ["Saved", formatBytes(compression.size_reduction_bytes)],
    ["Compression", `${compression.compression_percent}%`],
  ] : [["Output", "Ready"]];
  if (compression?.larger_than_original) rows.push(["Note", "Output is larger than the original."]);
  rows.forEach(([name, value]) => {
    const term = document.createElement("dt");
    term.textContent = name;
    const detail = document.createElement("dd");
    detail.textContent = value;
    stats.append(term, detail);
  });
  const link = $("download");
  link.href = downloadUrl(state.jobId);
  link.classList.remove("hidden");
}

function fail(message) {
  state.events?.close();
  setMessage(message || "Conversion failed.", "error");
  $("cancel").classList.add("hidden");
}

function cancelled() {
  state.events?.close();
  hide("result");
  $("download").classList.add("hidden");
  $("progress-title").textContent = "Cancelled";
  $("progress-copy").textContent = "Conversion cancelled.";
  $("cancel").classList.add("hidden");
}

async function cancelConversion() {
  try {
    if (state.batchId) await cancelBatch(state.batchId);
    else await cancel(state.jobId);
    cancelled();
  } catch (error) {
    setMessage(error.message, "error");
  }
}

async function restore(jobId) {
  try {
    const job = await getJob(jobId);
    state.jobId = jobId;
    if (job.status === "expired") {
      setMessage("That file is no longer available.", "error");
      return;
    }
    if (job.file && job.media) showMedia(job);
    if (job.status === "queued" || job.status === "processing") {
      showQueued(job.status);
      listen();
    }
    if (job.status === "completed") finish();
    if (job.status === "failed") fail(job.error);
    if (job.status === "cancelled") cancelled();
  } catch {
    sessionStorage.removeItem("videoforge-job");
  }
}

function show(id) { $(id).classList.remove("hidden"); }
function hide(id) { $(id).classList.add("hidden"); }
function setMessage(text, kind) {
  $("message").textContent = text;
  $("message").className = `status ${kind || ""}`;
}
