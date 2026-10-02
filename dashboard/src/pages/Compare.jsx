import { useCallback, useEffect, useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import { rangeParams } from "../lib/range";
import { useChartColors } from "../lib/useChartColors";
import TimeControls from "../components/TimeControls";

const VERTICAL_LABELS = { retail_showroom: "Retail showroom", bank_branch: "Bank branch", generic: "General" };

function Tip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-label">{label}</div>
      {payload.map((p) => (
        <div className="chart-tooltip-row" key={p.dataKey}>
          <span className="chart-legend-swatch" style={{ background: p.color }} />
          {p.name}: {p.value}
        </div>
      ))}
    </div>
  );
}

export default function Compare({ view, onOpenCamera }) {
  const { range, tick, setUpdatedAt } = view;
  const c = useChartColors();
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const requestId = useRef(0);

  const load = useCallback(async () => {
    const id = ++requestId.current;
    try {
      const result = await api.overview(rangeParams(range));
      if (id !== requestId.current) return;
      setRows(result);
      setError(null);
      setUpdatedAt(new Date());
    } catch (e) {
      if (id === requestId.current) setError(e.message);
    }
  }, [range, setUpdatedAt]);

  useEffect(() => {
    load();
  }, [load, tick]);

  const chartData = (rows || []).map((r) => ({ name: r.camera_name, In: r.total_in, Out: r.total_out }));

  return (
    <>
      <TimeControls {...view} showBucket={false} />
      {error && <div className="error-banner">{error}</div>}

      <section className="panel">
        <div className="panel-header">
          <div className="panel-title-group">
            <h2>Footfall by camera</h2>
            <span className="panel-subtitle">Entries and exits side by side for every camera in the selected time range</span>
          </div>
        </div>
        {chartData.length === 0 ? (
          <p className="empty">{rows ? "No cameras yet." : "Loading…"}</p>
        ) : (
          <>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={chartData} margin={{ top: 8, right: 12, bottom: 8, left: 0 }} barCategoryGap="35%" barGap={2}>
                <CartesianGrid stroke={c.gridline} vertical={false} />
                <XAxis dataKey="name" tick={{ fontSize: 11, fill: c.muted }} axisLine={{ stroke: c.gridline }} tickLine={false} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: c.muted }} axisLine={false} tickLine={false} width={28} />
                <Tooltip content={<Tip />} cursor={{ fill: c.gridline, opacity: 0.45 }} />
                <Bar dataKey="In" fill={c.series1} radius={[4, 4, 0, 0]} maxBarSize={24} isAnimationActive={false} />
                <Bar dataKey="Out" fill={c.series2} radius={[4, 4, 0, 0]} maxBarSize={24} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
            <div className="chart-legend">
              <span className="chart-legend-item">
                <span className="chart-legend-swatch" style={{ background: c.series1 }} /> In
              </span>
              <span className="chart-legend-item">
                <span className="chart-legend-swatch" style={{ background: c.series2 }} /> Out
              </span>
            </div>
          </>
        )}
      </section>

      <section className="panel">
        <div className="panel-header">
          <div className="panel-title-group">
            <h2>Site comparison</h2>
            <span className="panel-subtitle">Select a camera to open its full overview</span>
          </div>
        </div>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Camera</th>
                <th>Type</th>
                <th className="num">Entries</th>
                <th className="num">Exits</th>
                <th className="num">Zones</th>
                <th className="num">Zone visits</th>
                <th className="num">Avg dwell</th>
                <th className="num">Open alerts</th>
              </tr>
            </thead>
            <tbody>
              {(rows || []).map((r) => (
                <tr key={r.camera_id}>
                  <td>
                    <button type="button" className="link-button" onClick={() => onOpenCamera(r.camera_id)}>
                      {r.camera_name}
                    </button>
                  </td>
                  <td>{VERTICAL_LABELS[r.vertical] || r.vertical}</td>
                  <td className="num">{r.total_in}</td>
                  <td className="num">{r.total_out}</td>
                  <td className="num">{r.zones}</td>
                  <td className="num">{r.visits}</td>
                  <td className="num">{r.avg_dwell_seconds.toFixed(1)}s</td>
                  <td className="num">{r.open_alerts > 0 ? <span className="pill-count">{r.open_alerts}</span> : 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
