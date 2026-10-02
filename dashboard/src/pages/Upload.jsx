import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";

function fmt(iso) {
  return iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" }) : "–";
}

export default function Upload({ camera, reloadCameras }) {
  const inputRef = useRef(null);
  const [file, setFile] = useState(null);
  const [state, setState] = useState({ kind: "idle", message: "" }); // idle | uploading | done | error
  const [pending, setPending] = useState(null);
  const [videos, setVideos] = useState([]);

  const refresh = useCallback(async () => {
    try {
      const [queue, list] = await Promise.all([api.queue(), camera ? api.listVideos(camera.id) : Promise.resolve([])]);
      setPending(queue.pending);
      setVideos(list);
    } catch {
      setPending(null);
    }
  }, [camera?.id]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 3000);
    return () => clearInterval(timer);
  }, [refresh]);

  const upload = async () => {
    if (!camera || !file) return;
    setState({ kind: "uploading", message: `Uploading ${file.name}…` });
    try {
      await api.uploadVideo(camera.id, file);
      setState({ kind: "done", message: `${file.name} is queued. Results appear on the Overview once processing finishes.` });
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      await Promise.all([refresh(), reloadCameras()]);
    } catch (e) {
      setState({ kind: "error", message: e.message });
    }
  };

  if (!camera) return <p className="empty">Add a camera on the Setup page first.</p>;

  return (
    <>
      <section className="panel">
        <div className="panel-header">
          <div className="panel-title-group">
            <h2>Upload footage</h2>
            <span className="panel-subtitle">
              Analyse a recorded video for <strong>{camera.name}</strong>. Pick the camera at the top of the page to change it.
            </span>
          </div>
        </div>

        <div
          className={`dropzone ${file ? "has-file" : ""}`}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]);
          }}
        >
          <p className="dropzone-title">{file ? file.name : "Drop a video here"}</p>
          <p className="editor-muted">{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB` : "mp4, avi, mkv or mov"}</p>
          <label className="btn file-btn">
            {file ? "Choose another" : "Choose a file"}
            <input ref={inputRef} type="file" accept="video/*,.mkv" hidden onChange={(e) => setFile(e.target.files[0] || null)} />
          </label>
        </div>

        <div className="editor-card-actions">
          <button type="button" className="btn btn-primary" disabled={!file || state.kind === "uploading"} onClick={upload}>
            Upload and analyse
          </button>
          {state.message && <span className={`upload-status ${state.kind}`}>{state.message}</span>}
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div className="panel-title-group">
            <h2>Processing</h2>
            <span className="panel-subtitle">{pending === null ? "Checking the queue…" : pending === 0 ? "Nothing waiting — all uploads have been picked up." : `${pending} video${pending === 1 ? "" : "s"} waiting for a worker`}</span>
          </div>
        </div>
        {videos.length === 0 ? (
          <p className="empty">No processed videos for this camera yet.</p>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Recorded</th>
                  <th>Processed</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {videos.map((v) => (
                  <tr key={v.id}>
                    <td>{v.source_type.replace("_", " ")}</td>
                    <td>{fmt(v.recorded_at)}</td>
                    <td>{fmt(v.processed_at)}</td>
                    <td>
                      <span className={`status-pill ${v.status}`}>{v.status}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
