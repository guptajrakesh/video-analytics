import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useChartColors } from "../lib/useChartColors";
import { formatBucket } from "../lib/range";

// Counts per time bucket read best as bars: they stay legible with one or two
// buckets (a line would be a lone dot) and still scale to many.
function CustomTooltip({ active, payload, label }) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-label">{label}</div>
      {payload.map((entry) => (
        <div className="chart-tooltip-row" key={entry.dataKey}>
          <span className="chart-legend-swatch" style={{ background: entry.color }} />
          {entry.name}: {entry.value}
        </div>
      ))}
    </div>
  );
}

export default function FootfallChart({ data, bucketMinutes }) {
  const c = useChartColors();

  if (!data || data.length === 0) {
    return <p className="empty">No footfall recorded in this time range.</p>;
  }

  const chartData = data.map((d) => ({
    time: formatBucket(d.bucket_start, bucketMinutes),
    In: d.in_count,
    Out: d.out_count,
  }));

  return (
    <div>
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={chartData} margin={{ top: 8, right: 12, bottom: 8, left: 0 }} barCategoryGap="35%" barGap={2}>
          <CartesianGrid stroke={c.gridline} strokeWidth={1} vertical={false} />
          <XAxis dataKey="time" tick={{ fontSize: 11, fill: c.muted }} axisLine={{ stroke: c.gridline }} tickLine={false} />
          <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: c.muted }} axisLine={false} tickLine={false} width={28} />
          <Tooltip content={<CustomTooltip />} cursor={{ fill: c.gridline, opacity: 0.45 }} />
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
    </div>
  );
}
