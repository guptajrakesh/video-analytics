import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import Overview from "./pages/Overview";
import Compare from "./pages/Compare";
import Setup from "./pages/Setup";
import Upload from "./pages/Upload";
import "./App.css";
import "./screens.css";

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "compare", label: "Compare" },
  { id: "setup", label: "Setup" },
  { id: "upload", label: "Upload" },
];

function readHash() {
  const id = window.location.hash.replace("#", "");
  return TABS.some((t) => t.id === id) ? id : "overview";
}

function stored(key, fallback) {
  try {
    return window.localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

function store(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: the preference just won't persist */
  }
}

export default function App() {
  const [tab, setTab] = useState(readHash);
  const [cameras, setCameras] = useState(null);
  const [cameraId, setCameraId] = useState(() => stored("va.camera", ""));
  const [error, setError] = useState(null);

  const [range, setRange] = useState(() => stored("va.range", "all"));
  const [bucket, setBucket] = useState(() => Number(stored("va.bucket", "15")));
  const [autoRefresh, setAutoRefresh] = useState(() => stored("va.auto", "0") === "1");
  const [tick, setTick] = useState(0);
  const [updatedAt, setUpdatedAt] = useState(null);

  useEffect(() => {
    const onHash = () => setTab(readHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const reloadCameras = useCallback(async () => {
    try {
      const list = await api.listCameras();
      setCameras(list);
      setCameraId((current) => (list.some((c) => c.id === current) ? current : list[0]?.id || ""));
      setError(null);
    } catch (e) {
      setError(e.message);
      setCameras((c) => c ?? []);
    }
  }, []);

  useEffect(() => {
    reloadCameras();
  }, [reloadCameras]);

  useEffect(() => store("va.camera", cameraId), [cameraId]);
  useEffect(() => store("va.range", range), [range]);
  useEffect(() => store("va.bucket", String(bucket)), [bucket]);
  useEffect(() => store("va.auto", autoRefresh ? "1" : "0"), [autoRefresh]);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const timer = setInterval(() => setTick((t) => t + 1), 10000);
    return () => clearInterval(timer);
  }, [autoRefresh]);

  const goto = (id) => {
    window.location.hash = id;
    setTab(id);
  };

  const camera = cameras?.find((c) => c.id === cameraId) || null;

  const view = useMemo(
    () => ({
      range,
      onRange: setRange,
      bucket,
      onBucket: setBucket,
      autoRefresh,
      onAutoRefresh: setAutoRefresh,
      onRefresh: () => setTick((t) => t + 1),
      updatedAt,
      setUpdatedAt,
      tick,
    }),
    [range, bucket, autoRefresh, updatedAt, tick]
  );

  const openCamera = (id) => {
    setCameraId(id);
    goto("overview");
  };

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
              <path d="M3 7a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" stroke="white" strokeWidth="1.8" />
              <path d="M17 10.5 21 8v8l-4-2.5" stroke="white" strokeWidth="1.8" strokeLinejoin="round" />
            </svg>
          </span>
          <div className="brand-text">
            <span className="brand-title">Video Analytics</span>
            <span className="brand-subtitle">Retail &amp; branch intelligence</span>
          </div>
        </div>

        <nav className="tabs" aria-label="Sections">
          {TABS.map((t) => (
            <button key={t.id} type="button" className={tab === t.id ? "active" : ""} aria-current={tab === t.id ? "page" : undefined} onClick={() => goto(t.id)}>
              {t.label}
            </button>
          ))}
        </nav>

        {tab !== "compare" && cameras && cameras.length > 0 && (
          <div className="camera-picker">
            <label htmlFor="camera-select">Camera</label>
            <select id="camera-select" value={cameraId} onChange={(e) => setCameraId(e.target.value)}>
              {cameras.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
        )}
      </header>

      <main className="main">
        {error && <div className="error-banner">{error}</div>}
        {cameras === null && <p className="empty">Loading…</p>}

        {cameras !== null && tab === "overview" &&
          (camera ? (
            <Overview key={camera.id} camera={camera} view={view} />
          ) : (
            <div className="empty-state">
              <h2>No cameras yet</h2>
              <p>Add a camera, draw its zones, and upload some footage to see analytics here.</p>
              <button type="button" className="btn btn-primary" onClick={() => goto("setup")}>
                Set up your first camera
              </button>
            </div>
          ))}
        {cameras !== null && tab === "compare" && <Compare view={view} onOpenCamera={openCamera} />}
        {cameras !== null && tab === "setup" && <Setup cameras={cameras} camera={camera} onSelectCamera={setCameraId} reloadCameras={reloadCameras} />}
        {cameras !== null && tab === "upload" && <Upload camera={camera} reloadCameras={reloadCameras} />}
      </main>
    </div>
  );
}
