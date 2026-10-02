const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";
export const API_BASE_URL = API_BASE;

// Optional: URL of a demo webhook receiver that serves the alerts it received.
export const WEBHOOK_INBOX_URL = import.meta.env.VITE_WEBHOOK_INBOX_URL || "";

export async function fetchWebhookInbox() {
  const res = await fetch(`${WEBHOOK_INBOX_URL}/alerts`);
  if (!res.ok) throw new Error(`webhook inbox -> ${res.status}`);
  return res.json();
}

async function request(path, { method = "GET", json, form } = {}) {
  const options = { method, headers: {} };
  if (json !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(json);
  }
  if (form) options.body = form;

  const res = await fetch(`${API_BASE}${path}`, options);
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new Error(detail || `${method} ${path} failed (${res.status})`);
  }
  return res.status === 204 ? null : res.json();
}

function qs(params = {}) {
  const p = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") p.set(key, value);
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const api = {
  // cameras
  listCameras: () => request("/cameras"),
  createCamera: (body) => request("/cameras", { method: "POST", json: body }),
  updateCamera: (id, body) => request(`/cameras/${id}`, { method: "PATCH", json: body }),
  deleteCamera: (id) => request(`/cameras/${id}`, { method: "DELETE" }),
  setReferenceFrame: (id, file) => {
    const form = new FormData();
    form.append("file", file);
    return request(`/cameras/${id}/reference-frame`, { method: "POST", form });
  },
  referenceFrameUrl: (id, version = "") => `${API_BASE}/cameras/${id}/reference-frame${qs({ v: version })}`,

  // zones
  listZones: (cameraId) => request(`/cameras/${cameraId}/zones`),
  createZone: (body) => request("/zones", { method: "POST", json: body }),
  updateZone: (id, body) => request(`/zones/${id}`, { method: "PATCH", json: body }),
  deleteZone: (id) => request(`/zones/${id}`, { method: "DELETE" }),
  zoneTemplate: (vertical) => request(`/verticals/${vertical}/zone-template`),

  // analytics (all accept { start, end } ISO timestamps)
  footfallSummary: (cameraId, range) => request(`/cameras/${cameraId}/footfall${qs(range)}`),
  footfallTimeseries: (cameraId, intervalMinutes, range) =>
    request(`/cameras/${cameraId}/footfall-timeseries${qs({ interval_minutes: intervalMinutes, ...range })}`),
  zonesOccupancy: (cameraId) => request(`/cameras/${cameraId}/zones/occupancy`),
  zoneDwell: (zoneId, range) => request(`/zones/${zoneId}/dwell${qs(range)}`),
  heatmap: (cameraId, range, cols = 32, rows = 18) => request(`/cameras/${cameraId}/heatmap${qs({ cols, rows, ...range })}`),
  overview: (range) => request(`/overview${qs(range)}`),

  // alerts
  alerts: (cameraId, { status, limit = 100, ...range } = {}) => request(`/cameras/${cameraId}/alerts${qs({ status, limit, ...range })}`),
  setAlertStatus: (alertId, status) => request(`/alerts/${alertId}`, { method: "PATCH", json: { status } }),

  // videos
  uploadVideo: (cameraId, file) => {
    const form = new FormData();
    form.append("camera_id", cameraId);
    form.append("file", file);
    return request("/videos/upload", { method: "POST", form });
  },
  listVideos: (cameraId, limit = 10) => request(`/videos${qs({ camera_id: cameraId, limit })}`),
  queue: () => request("/queue"),
};
