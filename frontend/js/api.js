const API = "/api";

export function friendlyError(status, detail) {
  const text = typeof detail === "string" ? detail : detail?.detail || "";
  if (status === 413) return "This file exceeds the server's 20 GiB upload limit.";
  if (status === 404) return "That job was not found.";
  if (status === 409) return text || "This job is already queued, processing, or completed.";
  if (status === 422) return text || "Those conversion settings are not valid.";
  if (status === 503) return "The conversion queue is temporarily unavailable.";
  if (status === 507) return "The server does not have enough free storage.";
  if (status === 400) return text || "The request was not valid.";
  return text || "The server could not complete that request.";
}

export async function getCapabilities() {
  const response = await fetch(`${API}/capabilities`);
  if (!response.ok) throw new Error(friendlyError(response.status));
  return response.json();
}

export function upload(file, onProgress) {
  return new Promise((resolve, reject) => {
    const body = new FormData();
    body.append("file", file, file.name);
    const request = new XMLHttpRequest();
    request.open("POST", `${API}/jobs`);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded, event.total);
    };
    request.onerror = () => reject(new Error("The upload connection failed."));
    request.ontimeout = () => reject(new Error("The upload timed out."));
    request.onload = () => {
      const payload = safeJson(request.responseText);
      if (request.status >= 200 && request.status < 300) resolve(payload);
      else reject(new Error(friendlyError(request.status, payload)));
    };
    request.send(body);
  });
}

export async function validate(jobId, settings) {
  return postJson(`${API}/jobs/${jobId}/convert/validate`, settings);
}

export async function convert(jobId, settings) {
  return postJson(`${API}/jobs/${jobId}/convert`, settings);
}

export async function cancel(jobId) {
  return postJson(`${API}/jobs/${jobId}/cancel`, {});
}

export async function getJob(jobId) {
  const response = await fetch(`${API}/jobs/${jobId}`);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(friendlyError(response.status, payload));
  return payload;
}

export function events(jobId, onEvent) {
  const source = new EventSource(`${API}/jobs/${jobId}/events`);
  const handle = (event) => onEvent(event.type, JSON.parse(event.data));
  ["progress", "completed", "failed", "cancelled"].forEach((name) => source.addEventListener(name, handle));
  source.onerror = () => onEvent("error", {});
  return source;
}

export function downloadUrl(jobId) {
  return `${API}/jobs/${jobId}/download`;
}

export async function createBatch(jobIds, settings) {
  return postJson(`${API}/batches`, { ...settings, job_ids: jobIds });
}

export function batchEvents(batchId, onEvent) {
  const source = new EventSource(`${API}/batches/${batchId}/events`);
  const handle = (event) => onEvent(event.type, JSON.parse(event.data));
  ["progress", "completed", "completed_with_errors", "failed", "cancelled"].forEach((name) => source.addEventListener(name, handle));
  source.onerror = () => onEvent("error", {});
  return source;
}

export async function cancelBatch(batchId) {
  return postJson(`${API}/batches/${batchId}/cancel`, {});
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(friendlyError(response.status, body));
  return body;
}

function safeJson(text) {
  try { return JSON.parse(text); } catch { return {}; }
}
