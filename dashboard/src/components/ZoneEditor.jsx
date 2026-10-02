import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { newId } from "../lib/range";

const TYPE_LABELS = {
  entrance: "Entrance area",
  display_section: "Display section",
  billing_counter: "Billing counter",
  lobby: "Lobby",
  teller_counter: "Teller counter",
  generic: "General area",
};

// Categorical slots 1, 3, 5, 7 — zone outlines need to stay distinguishable.
const ZONE_COLORS = ["#2a78d6", "#1baf7a", "#e87ba4", "#9085e9"];

const EMPTY_FORM = { name: "", zone_type: "generic", wait: "", occ: "", cooldown: "60" };

// The pipeline counts a crossing as "in" when the person ends up on the side
// of the line given by the normal (-dy, dx) of its p1 -> p2 direction.
function inNormal([[x1, y1], [x2, y2]]) {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len = Math.hypot(dx, dy) || 1;
  return [-dy / len, dx / len];
}

const num = (v) => (v === "" || v === null || v === undefined ? null : Number(v));
const pts = (points) => points.map((p) => p.join(",")).join(" ");

export default function ZoneEditor({ camera, zones, onChanged, onError }) {
  const svgRef = useRef(null);
  const [typeOptions, setTypeOptions] = useState(["generic"]);
  const [frameVersion, setFrameVersion] = useState(Date.now());
  const [frameBroken, setFrameBroken] = useState(false);
  const [dims, setDims] = useState(null);

  const [mode, setMode] = useState("select"); // select | zone | line
  const [draft, setDraft] = useState([]);
  const [cursor, setCursor] = useState(null);
  const [selectedId, setSelectedId] = useState(null); // zone id | "new" | null
  const [pendingPolygon, setPendingPolygon] = useState(null);
  const [redrawing, setRedrawing] = useState(false);
  const [pendingLine, setPendingLine] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [editPoly, setEditPoly] = useState(null); // working copy of the selected zone's corners
  const [dragIndex, setDragIndex] = useState(null);

  const showFrame = camera.has_reference_frame && !frameBroken;

  useEffect(() => {
    api
      .zoneTemplate(camera.vertical)
      .then((t) => setTypeOptions([...t.zone_types, "generic"].filter((v, i, a) => a.indexOf(v) === i)))
      .catch(() => setTypeOptions(["generic"]));
  }, [camera.vertical]);

  // Reset the working state when switching cameras.
  useEffect(() => {
    setMode("select");
    setDraft([]);
    setSelectedId(null);
    setPendingPolygon(null);
    setPendingLine(null);
    setRedrawing(false);
    setFrameBroken(false);
    setDims(null);
  }, [camera.id]);

  const selectedZone = zones.find((z) => z.id === selectedId) || null;

  useEffect(() => {
    if (selectedZone) {
      setForm({
        name: selectedZone.name,
        zone_type: selectedZone.zone_type,
        wait: selectedZone.wait_time_alert_seconds ?? "",
        occ: selectedZone.max_occupancy_alert ?? "",
        cooldown: selectedZone.alert_cooldown_seconds ?? 60,
      });
    }
  }, [selectedZone?.id, selectedZone?.name, selectedZone?.zone_type, selectedZone?.wait_time_alert_seconds, selectedZone?.max_occupancy_alert, selectedZone?.alert_cooldown_seconds]);

  const selectedPolygonKey = selectedZone ? JSON.stringify(selectedZone.polygon) : "";
  useEffect(() => {
    setEditPoly(selectedZone ? selectedZone.polygon.map((p) => [...p]) : null);
    setDragIndex(null);
  }, [selectedZone?.id, selectedPolygonKey]);

  const polygonDirty = Boolean(selectedZone && editPoly && JSON.stringify(editPoly) !== selectedPolygonKey);

  useEffect(() => {
    if (dragIndex === null) return undefined;
    const move = (e) => {
      const p = toFrame(e);
      setEditPoly((poly) => poly.map((q, i) => (i === dragIndex ? p : q)));
    };
    const end = () => setDragIndex(null);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
    };
  }, [dragIndex]);

  const addCornerAfter = (i, e) => {
    e.stopPropagation();
    e.preventDefault();
    const a = editPoly[i];
    const b = editPoly[(i + 1) % editPoly.length];
    const mid = [Math.round(((a[0] + b[0]) / 2) * 10) / 10, Math.round(((a[1] + b[1]) / 2) * 10) / 10];
    setEditPoly([...editPoly.slice(0, i + 1), mid, ...editPoly.slice(i + 1)]);
    setDragIndex(i + 1);
  };

  const removeCorner = (i, e) => {
    e.stopPropagation();
    if (editPoly.length > 3) setEditPoly(editPoly.filter((_, j) => j !== i));
  };

  const cancelDrawing = () => {
    setMode("select");
    setDraft([]);
    setCursor(null);
    setRedrawing(false);
  };

  useEffect(() => {
    if (mode === "select") return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") cancelDrawing();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode]);

  const toFrame = (e) => {
    const r = svgRef.current.getBoundingClientRect();
    const x = ((e.clientX - r.left) / r.width) * dims.w;
    const y = ((e.clientY - r.top) / r.height) * dims.h;
    return [Math.round(Math.min(dims.w, Math.max(0, x)) * 10) / 10, Math.round(Math.min(dims.h, Math.max(0, y)) * 10) / 10];
  };

  const unit = dims ? dims.w / 200 : 3;

  const finishZone = async (points) => {
    if (points.length < 3) return;
    if (redrawing && selectedZone) {
      setBusy(true);
      try {
        await api.updateZone(selectedZone.id, { polygon: points });
        await onChanged();
      } catch (err) {
        onError(err.message);
      } finally {
        setBusy(false);
      }
      cancelDrawing();
      return;
    }
    setPendingPolygon(points);
    setSelectedId("new");
    setForm({ ...EMPTY_FORM, zone_type: typeOptions[0] || "generic" });
    cancelDrawing();
  };

  const onStageClick = (e) => {
    if (!dims || mode === "select") return;
    const p = toFrame(e);
    if (mode === "zone") {
      if (draft.length >= 3 && Math.hypot(p[0] - draft[0][0], p[1] - draft[0][1]) < unit * 4) {
        finishZone(draft);
        return;
      }
      setDraft([...draft, p]);
    } else if (mode === "line") {
      const next = [...draft, p];
      if (next.length === 2) {
        setPendingLine(next);
        cancelDrawing();
      } else {
        setDraft(next);
      }
    }
  };

  const startDrawZone = () => {
    setSelectedId(null);
    setPendingPolygon(null);
    setRedrawing(false);
    setDraft([]);
    setMode("zone");
  };

  const startRedraw = () => {
    setRedrawing(true);
    setDraft([]);
    setMode("zone");
  };

  const startDrawLine = () => {
    setPendingLine(null);
    setDraft([]);
    setMode("line");
  };

  const saveZone = async () => {
    if (!form.name.trim()) {
      onError("Give the zone a name.");
      return;
    }
    const common = {
      name: form.name.trim(),
      zone_type: form.zone_type,
      wait_time_alert_seconds: num(form.wait),
      max_occupancy_alert: num(form.occ),
      alert_cooldown_seconds: num(form.cooldown) ?? 60,
    };
    setBusy(true);
    try {
      if (selectedId === "new") {
        await api.createZone({ id: newId("zone", form.name), camera_id: camera.id, polygon: pendingPolygon, ...common });
        setPendingPolygon(null);
        setSelectedId(null);
      } else {
        await api.updateZone(selectedId, polygonDirty ? { ...common, polygon: editPoly } : common);
      }
      await onChanged();
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const removeZone = async () => {
    if (!window.confirm(`Delete zone "${selectedZone.name}"? Its recorded visits and alerts are deleted too.`)) return;
    setBusy(true);
    try {
      await api.deleteZone(selectedZone.id);
      setSelectedId(null);
      await onChanged();
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const discardNewZone = () => {
    setPendingPolygon(null);
    setSelectedId(null);
  };

  const saveLine = async (line) => {
    setBusy(true);
    try {
      await api.updateCamera(camera.id, { entrance_line: line });
      setPendingLine(null);
      await onChanged();
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const uploadFrame = async (file) => {
    if (!file) return;
    setBusy(true);
    try {
      await api.setReferenceFrame(camera.id, file);
      setFrameBroken(false);
      setDims(null);
      setFrameVersion(Date.now());
      await onChanged();
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const line = pendingLine || camera.entrance_line;
  const lineNormal = line ? inNormal(line) : null;
  const hint =
    mode === "zone"
      ? draft.length < 3
        ? "Click on the picture to place corners of the zone. Press Esc to cancel."
        : "Keep clicking to add corners, then click the first point (or press Finish) to close the shape."
      : mode === "line"
        ? draft.length === 0
          ? "Click where the entrance line starts."
          : "Click where the entrance line ends."
        : selectedZone
          ? "Drag a corner to reshape the zone. Click a faint dot on an edge to add a corner; double-click a corner to remove it. Then Save changes."
          : "Choose a tool, or click a zone to edit it.";

  if (!showFrame) {
    return (
      <div className="editor-empty">
        <h3>No camera picture yet</h3>
        <p>
          Zones are drawn on a still frame from the camera. Upload a video for this camera on the Upload page and a frame is taken
          automatically, or add one here — an image or a short video clip.
        </p>
        <label className="btn btn-primary file-btn">
          Choose image or video
          <input type="file" accept="image/*,video/*" hidden disabled={busy} onChange={(e) => uploadFrame(e.target.files[0])} />
        </label>
      </div>
    );
  }

  return (
    <div className="editor">
      <div className="editor-main">
        <div className="editor-tools">
          <div className="segmented segmented-small" role="group" aria-label="Tool">
            <button type="button" className={mode === "select" ? "active" : ""} onClick={cancelDrawing}>
              Select
            </button>
            <button type="button" className={mode === "zone" && !redrawing ? "active" : ""} onClick={startDrawZone}>
              Draw zone
            </button>
            <button type="button" className={mode === "line" ? "active" : ""} onClick={startDrawLine}>
              Entrance line
            </button>
          </div>
          {mode === "zone" && (
            <div className="editor-draw-actions">
              <button type="button" className="btn btn-small" disabled={draft.length < 3} onClick={() => finishZone(draft)}>
                Finish shape
              </button>
              <button type="button" className="btn btn-small btn-ghost" disabled={draft.length === 0} onClick={() => setDraft(draft.slice(0, -1))}>
                Undo point
              </button>
              <button type="button" className="btn btn-small btn-ghost" onClick={cancelDrawing}>
                Cancel
              </button>
            </div>
          )}
        </div>
        <p className="editor-hint">{hint}</p>

        <div className={`editor-stage ${mode !== "select" ? "drawing" : ""}`} style={{ aspectRatio: dims ? `${dims.w} / ${dims.h}` : "16 / 9" }}>
          <img
            src={api.referenceFrameUrl(camera.id, frameVersion)}
            alt={`${camera.name} reference frame`}
            className="editor-frame"
            onLoad={(e) => setDims({ w: e.target.naturalWidth, h: e.target.naturalHeight })}
            onError={() => setFrameBroken(true)}
            draggable={false}
          />
          {dims && (
            <svg
              ref={svgRef}
              className="editor-svg"
              viewBox={`0 0 ${dims.w} ${dims.h}`}
              onClick={onStageClick}
              onMouseMove={(e) => mode !== "select" && setCursor(toFrame(e))}
              onMouseLeave={() => setCursor(null)}
            >
              {zones.map((z, i) => {
                const color = ZONE_COLORS[i % ZONE_COLORS.length];
                const selected = z.id === selectedId;
                return (
                  <polygon
                    key={z.id}
                    points={pts(selected && editPoly ? editPoly : z.polygon)}
                    fill={color}
                    fillOpacity={selected ? 0.32 : 0.16}
                    stroke={color}
                    strokeWidth={selected ? 3 : 2}
                    vectorEffect="non-scaling-stroke"
                    style={{ cursor: mode === "select" ? "pointer" : "crosshair", pointerEvents: mode === "select" ? "all" : "none" }}
                    onClick={(e) => {
                      if (mode !== "select") return;
                      e.stopPropagation();
                      setPendingPolygon(null);
                      setSelectedId(z.id);
                    }}
                  />
                );
              })}
              {zones.map((z) => {
                const shape = z.id === selectedId && editPoly ? editPoly : z.polygon;
                const cx = shape.reduce((s, p) => s + p[0], 0) / shape.length;
                const cy = shape.reduce((s, p) => s + p[1], 0) / shape.length;
                return (
                  <text key={`t-${z.id}`} x={cx} y={cy} textAnchor="middle" fontSize={dims.w / 45} fill="#fff" stroke="rgba(0,0,0,.75)" strokeWidth={dims.w / 300} paintOrder="stroke" pointerEvents="none">
                    {z.name}
                  </text>
                );
              })}

              {mode === "select" && selectedZone && editPoly && (
                <g>
                  {editPoly.map((p, i) => {
                    const q = editPoly[(i + 1) % editPoly.length];
                    return (
                      <circle
                        key={`m-${i}`}
                        cx={(p[0] + q[0]) / 2}
                        cy={(p[1] + q[1]) / 2}
                        r={unit * 1.1}
                        fill="#fff"
                        fillOpacity={0.55}
                        stroke="#222"
                        strokeWidth={1}
                        vectorEffect="non-scaling-stroke"
                        style={{ cursor: "copy", touchAction: "none" }}
                        onPointerDown={(e) => addCornerAfter(i, e)}
                      >
                        <title>Add a corner here</title>
                      </circle>
                    );
                  })}
                  {editPoly.map((p, i) => (
                    <circle
                      key={`c-${i}`}
                      cx={p[0]}
                      cy={p[1]}
                      r={unit * 1.7}
                      fill="#fff"
                      stroke="#2a78d6"
                      strokeWidth={2.5}
                      vectorEffect="non-scaling-stroke"
                      style={{ cursor: "grab", touchAction: "none" }}
                      onPointerDown={(e) => {
                        e.stopPropagation();
                        e.preventDefault();
                        setDragIndex(i);
                      }}
                      onDoubleClick={(e) => removeCorner(i, e)}
                    >
                      <title>Drag to move. Double-click to remove.</title>
                    </circle>
                  ))}
                </g>
              )}

              {pendingPolygon && <polygon points={pts(pendingPolygon)} fill="#eda100" fillOpacity={0.28} stroke="#eda100" strokeWidth={3} strokeDasharray="6 4" vectorEffect="non-scaling-stroke" pointerEvents="none" />}

              {line && (
                <g pointerEvents="none">
                  <line x1={line[0][0]} y1={line[0][1]} x2={line[1][0]} y2={line[1][1]} stroke="#eb6834" strokeWidth={4} vectorEffect="non-scaling-stroke" strokeDasharray={pendingLine ? "8 5" : undefined} />
                  {(() => {
                    const mx = (line[0][0] + line[1][0]) / 2;
                    const my = (line[0][1] + line[1][1]) / 2;
                    const ax = mx + lineNormal[0] * unit * 14;
                    const ay = my + lineNormal[1] * unit * 14;
                    return (
                      <>
                        <line x1={mx} y1={my} x2={ax} y2={ay} stroke="#eb6834" strokeWidth={3} vectorEffect="non-scaling-stroke" />
                        <circle cx={ax} cy={ay} r={unit * 1.6} fill="#eb6834" />
                        <text x={ax + lineNormal[0] * unit * 6} y={ay + lineNormal[1] * unit * 6 + unit * 1.5} textAnchor="middle" fontSize={dims.w / 50} fontWeight="700" fill="#fff" stroke="rgba(0,0,0,.8)" strokeWidth={dims.w / 300} paintOrder="stroke">
                          IN
                        </text>
                      </>
                    );
                  })()}
                </g>
              )}

              {draft.length > 0 && (
                <g pointerEvents="none">
                  <polyline points={pts(cursor ? [...draft, cursor] : draft)} fill="none" stroke="#eda100" strokeWidth={2.5} vectorEffect="non-scaling-stroke" />
                  {draft.map((p, i) => (
                    <circle key={i} cx={p[0]} cy={p[1]} r={i === 0 && mode === "zone" && draft.length >= 3 ? unit * 2.2 : unit * 1.3} fill="#eda100" stroke="#fff" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
                  ))}
                </g>
              )}
            </svg>
          )}
        </div>
      </div>

      <aside className="editor-side">
        {selectedId ? (
          <div className="editor-card">
            <h3>{selectedId === "new" ? "New zone" : "Edit zone"}</h3>
            <label className="field">
              <span>Name</span>
              <input type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Teller counter" />
            </label>
            <label className="field">
              <span>Type</span>
              <select value={form.zone_type} onChange={(e) => setForm({ ...form, zone_type: e.target.value })}>
                {typeOptions.map((t) => (
                  <option key={t} value={t}>
                    {TYPE_LABELS[t] || t}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Alert if one visit lasts longer than</span>
              <div className="field-unit">
                <input type="number" min="1" value={form.wait} onChange={(e) => setForm({ ...form, wait: e.target.value })} placeholder="no limit" />
                <em>seconds</em>
              </div>
            </label>
            <label className="field">
              <span>Alert if more than this many people are inside</span>
              <div className="field-unit">
                <input type="number" min="1" value={form.occ} onChange={(e) => setForm({ ...form, occ: e.target.value })} placeholder="no limit" />
                <em>people</em>
              </div>
            </label>
            <label className="field">
              <span>Group repeat alerts within</span>
              <div className="field-unit">
                <input type="number" min="0" value={form.cooldown} onChange={(e) => setForm({ ...form, cooldown: e.target.value })} />
                <em>seconds</em>
              </div>
            </label>
            <div className="editor-card-actions">
              <button type="button" className="btn btn-primary" disabled={busy} onClick={saveZone}>
                {selectedId === "new" ? "Add zone" : "Save changes"}
              </button>
              {selectedId === "new" ? (
                <button type="button" className="btn btn-ghost" onClick={discardNewZone}>
                  Discard
                </button>
              ) : (
                <>
                  {polygonDirty && (
                    <button type="button" className="btn btn-ghost" onClick={() => setEditPoly(selectedZone.polygon.map((p) => [...p]))}>
                      Reset shape
                    </button>
                  )}
                  <button type="button" className="btn" disabled={busy} onClick={startRedraw}>
                    Redraw shape
                  </button>
                  <button type="button" className="btn btn-danger" disabled={busy} onClick={removeZone}>
                    Delete
                  </button>
                </>
              )}
            </div>
          </div>
        ) : (
          <div className="editor-card">
            <h3>Zones</h3>
            {zones.length === 0 ? (
              <p className="editor-muted">No zones yet. Choose “Draw zone” and outline an area such as a counter or display.</p>
            ) : (
              <ul className="editor-zone-list">
                {zones.map((z, i) => (
                  <li key={z.id}>
                    <button type="button" onClick={() => setSelectedId(z.id)}>
                      <span className="editor-zone-dot" style={{ background: ZONE_COLORS[i % ZONE_COLORS.length] }} />
                      <span className="editor-zone-name">{z.name}</span>
                      <span className="editor-zone-type">{TYPE_LABELS[z.zone_type] || z.zone_type}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        <div className="editor-card">
          <h3>Entrance line</h3>
          <p className="editor-muted">People crossing this line are counted as entries or exits. The arrow marks the “in” direction.</p>
          {pendingLine ? (
            <div className="editor-card-actions">
              <button type="button" className="btn btn-primary" disabled={busy} onClick={() => saveLine(pendingLine)}>
                Save line
              </button>
              <button type="button" className="btn" onClick={() => setPendingLine([pendingLine[1], pendingLine[0]])}>
                Flip direction
              </button>
              <button type="button" className="btn btn-ghost" onClick={() => setPendingLine(null)}>
                Discard
              </button>
            </div>
          ) : camera.entrance_line ? (
            <div className="editor-card-actions">
              <button type="button" className="btn" disabled={busy} onClick={() => saveLine([camera.entrance_line[1], camera.entrance_line[0]])}>
                Flip direction
              </button>
              <button type="button" className="btn" onClick={startDrawLine}>
                Redraw
              </button>
              <button type="button" className="btn btn-danger" disabled={busy} onClick={() => saveLine(null)}>
                Remove
              </button>
            </div>
          ) : (
            <button type="button" className="btn" onClick={startDrawLine}>
              Draw entrance line
            </button>
          )}
        </div>

        <div className="editor-card">
          <h3>Camera picture</h3>
          <p className="editor-muted">Zones are saved in this picture’s pixel space. Replace it only with a frame at the same resolution.</p>
          <label className="btn file-btn">
            Replace picture
            <input type="file" accept="image/*,video/*" hidden disabled={busy} onChange={(e) => uploadFrame(e.target.files[0])} />
          </label>
        </div>
      </aside>
    </div>
  );
}
