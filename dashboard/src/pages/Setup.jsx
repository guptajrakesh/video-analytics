import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { newId } from "../lib/range";
import ZoneEditor from "../components/ZoneEditor";

const VERTICALS = [
  { id: "retail_showroom", label: "Retail showroom" },
  { id: "bank_branch", label: "Bank branch" },
  { id: "generic", label: "Other" },
];

function CameraForm({ initial, onSave, onCancel, onDelete, busy }) {
  const [name, setName] = useState(initial?.name || "");
  const [vertical, setVertical] = useState(initial?.vertical || "retail_showroom");
  const [rtsp, setRtsp] = useState(initial?.rtsp_url || "");

  return (
    <form
      className="camera-form"
      onSubmit={(e) => {
        e.preventDefault();
        onSave({ name: name.trim(), vertical, rtsp_url: rtsp.trim() || null });
      }}
    >
      <label className="field">
        <span>Camera name</span>
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Main entrance" required />
      </label>
      <label className="field">
        <span>Site type</span>
        <select value={vertical} onChange={(e) => setVertical(e.target.value)}>
          {VERTICALS.map((v) => (
            <option key={v.id} value={v.id}>
              {v.label}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>Live stream address (optional)</span>
        <input type="text" value={rtsp} onChange={(e) => setRtsp(e.target.value)} placeholder="rtsp://camera-ip/stream" />
      </label>
      <div className="editor-card-actions">
        <button type="submit" className="btn btn-primary" disabled={busy || !name.trim()}>
          {initial ? "Save camera" : "Add camera"}
        </button>
        <button type="button" className="btn btn-ghost" onClick={onCancel}>
          Cancel
        </button>
        {onDelete && (
          <button type="button" className="btn btn-danger" disabled={busy} onClick={onDelete}>
            Delete camera
          </button>
        )}
      </div>
    </form>
  );
}

export default function Setup({ cameras, camera, onSelectCamera, reloadCameras }) {
  const [zones, setZones] = useState([]);
  const [formMode, setFormMode] = useState(null); // null | "new" | "edit"
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const loadZones = useCallback(async () => {
    if (!camera) return setZones([]);
    try {
      setZones(await api.listZones(camera.id));
    } catch (e) {
      setError(e.message);
    }
  }, [camera?.id]);

  useEffect(() => {
    setFormMode(null);
    setError(null);
    loadZones();
  }, [loadZones]);

  const onChanged = async () => {
    setError(null);
    await Promise.all([loadZones(), reloadCameras()]);
  };

  const saveCamera = async (fields) => {
    setBusy(true);
    try {
      if (formMode === "new") {
        const created = await api.createCamera({ id: newId("cam", fields.name), ...fields });
        await reloadCameras();
        onSelectCamera(created.id);
      } else {
        await api.updateCamera(camera.id, fields);
        await reloadCameras();
      }
      setFormMode(null);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const deleteCamera = async () => {
    if (!window.confirm(`Delete camera "${camera.name}"? All of its zones, recorded results and alerts are deleted too. This cannot be undone.`)) return;
    setBusy(true);
    try {
      await api.deleteCamera(camera.id);
      setFormMode(null);
      await reloadCameras();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {error && <div className="error-banner">{error}</div>}

      <section className="panel">
        <div className="panel-header">
          <div className="panel-title-group">
            <h2>Cameras</h2>
            <span className="panel-subtitle">Add a camera, then draw its zones and entrance line on a frame from its footage</span>
          </div>
          <div className="panel-actions">
            {camera && formMode !== "edit" && (
              <button type="button" className="btn" onClick={() => setFormMode("edit")}>
                Edit camera
              </button>
            )}
            {formMode !== "new" && (
              <button type="button" className="btn btn-primary" onClick={() => setFormMode("new")}>
                Add camera
              </button>
            )}
          </div>
        </div>

        {formMode === "new" && <CameraForm onSave={saveCamera} onCancel={() => setFormMode(null)} busy={busy} />}
        {formMode === "edit" && camera && <CameraForm initial={camera} onSave={saveCamera} onCancel={() => setFormMode(null)} onDelete={deleteCamera} busy={busy} />}

        {cameras.length === 0 ? (
          <p className="empty">No cameras yet. Add your first camera to get started.</p>
        ) : (
          <div className="camera-chips">
            {cameras.map((c) => (
              <button key={c.id} type="button" className={`camera-chip ${c.id === camera?.id ? "active" : ""}`} onClick={() => onSelectCamera(c.id)}>
                <span className="camera-chip-name">{c.name}</span>
                <span className="camera-chip-meta">{c.has_reference_frame ? "picture ready" : "needs a picture"}</span>
              </button>
            ))}
          </div>
        )}
      </section>

      {camera && (
        <section className="panel">
          <div className="panel-header">
            <div className="panel-title-group">
              <h2>Zones &amp; entrance line — {camera.name}</h2>
              <span className="panel-subtitle">Changes apply to videos processed from now on; results already recorded are not recalculated</span>
            </div>
          </div>
          <ZoneEditor camera={camera} zones={zones} onChanged={onChanged} onError={setError} />
        </section>
      )}
    </>
  );
}
