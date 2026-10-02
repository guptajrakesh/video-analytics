import { useEffect, useState } from "react";

// Without a camera view: sequential ramp, single hue light -> dark. The
// lightest step recedes toward the panel surface, so zero density doesn't pop
// as bright white on a dark panel.
const RAMP_LIGHT = ["#fcfcfb", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
const RAMP_DARK = ["#1a1a19", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb", "#fcfcfb"];

function useIsDarkMode() {
  const [isDark, setIsDark] = useState(() => window.matchMedia("(prefers-color-scheme: dark)").matches);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = (e) => setIsDark(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);
  return isDark;
}

function rampColor(intensity, ramp) {
  return ramp[Math.min(ramp.length - 1, Math.round(intensity * (ramp.length - 1)))];
}

function centroid(polygon) {
  const n = polygon.length;
  return [polygon.reduce((s, p) => s + p[0], 0) / n, polygon.reduce((s, p) => s + p[1], 0) / n];
}

/**
 * Where people spend time. With a reference frame it is drawn over the real
 * camera view (density as translucent blue), optionally with the zones and the
 * entrance line, so the numbers can be read against the actual scene.
 */
export default function Heatmap({ heatmap, frameUrl, zones = [], entranceLine = null }) {
  const isDark = useIsDarkMode();
  const [frameFailed, setFrameFailed] = useState(false);
  const [showFrame, setShowFrame] = useState(true);
  const [showZones, setShowZones] = useState(true);

  useEffect(() => setFrameFailed(false), [frameUrl]);

  if (!heatmap || !heatmap.counts || heatmap.counts.length === 0) {
    return <p className="empty">No position data in this time range.</p>;
  }

  const hasFrame = Boolean(frameUrl) && !frameFailed;
  const overFrame = hasFrame && showFrame;
  const ramp = isDark ? RAMP_DARK : RAMP_LIGHT;
  const max = Math.max(1, ...heatmap.counts.flat());
  const W = heatmap.frame_width;
  const H = heatmap.frame_height;
  const pct = (x, total) => `${(x / total) * 100}%`;

  return (
    <div className="heatmap-wrap">
      <div className="heatmap-stage" style={{ aspectRatio: `${W} / ${H}` }}>
        {hasFrame && (
          <img
            className="heatmap-frame"
            src={frameUrl}
            alt=""
            style={{ visibility: showFrame ? "visible" : "hidden" }}
            onError={() => setFrameFailed(true)}
          />
        )}

        <div className="heatmap-grid" style={{ gridTemplateColumns: `repeat(${heatmap.cols}, 1fr)`, background: overFrame ? "transparent" : undefined }}>
          {heatmap.counts.flat().map((count, i) => {
            const intensity = count / max;
            const background = overFrame ? (count === 0 ? "transparent" : `rgba(37, 99, 235, ${(0.18 + intensity * 0.67).toFixed(2)})`) : rampColor(intensity, ramp);
            return <div key={i} className="heatmap-cell" style={{ backgroundColor: background }} title={`${count} sample${count === 1 ? "" : "s"}`} />;
          })}
        </div>

        {showZones && (zones.length > 0 || entranceLine) && (
          <>
            <svg className="heatmap-overlay" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
              {zones.map((z) => (
                <polygon
                  key={z.id}
                  points={z.polygon.map((p) => p.join(",")).join(" ")}
                  fill="none"
                  stroke="#ffffff"
                  strokeWidth="2"
                  vectorEffect="non-scaling-stroke"
                  strokeDasharray="6 4"
                  style={{ filter: "drop-shadow(0 0 1px rgba(0,0,0,.9))" }}
                />
              ))}
              {entranceLine && (
                <line
                  x1={entranceLine[0][0]}
                  y1={entranceLine[0][1]}
                  x2={entranceLine[1][0]}
                  y2={entranceLine[1][1]}
                  stroke="#eb6834"
                  strokeWidth="3"
                  vectorEffect="non-scaling-stroke"
                  style={{ filter: "drop-shadow(0 0 1px rgba(0,0,0,.9))" }}
                />
              )}
            </svg>
            {zones.map((z) => {
              const [cx, cy] = centroid(z.polygon);
              return (
                <span key={z.id} className="heatmap-zone-label" style={{ left: pct(cx, W), top: pct(cy, H) }}>
                  {z.name}
                </span>
              );
            })}
          </>
        )}
      </div>

      <div className="heatmap-footer">
        <div className="heatmap-scale">
          <span>Low traffic</span>
          <span
            className="heatmap-scale-track"
            style={{ background: overFrame ? "linear-gradient(to right, rgba(37,99,235,.18), rgba(37,99,235,.85))" : `linear-gradient(to right, ${ramp[0]}, ${ramp[ramp.length - 1]})` }}
          />
          <span>High traffic</span>
        </div>
        <div className="heatmap-toggles">
          {hasFrame && (
            <label className="toolbar-check">
              <input type="checkbox" checked={showFrame} onChange={(e) => setShowFrame(e.target.checked)} />
              Camera view
            </label>
          )}
          {(zones.length > 0 || entranceLine) && (
            <label className="toolbar-check">
              <input type="checkbox" checked={showZones} onChange={(e) => setShowZones(e.target.checked)} />
              Zones &amp; entrance line
            </label>
          )}
        </div>
      </div>
    </div>
  );
}
