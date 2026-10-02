import { BUCKETS, RANGE_PRESETS } from "../lib/range";

export default function TimeControls({ range, onRange, bucket, onBucket, autoRefresh, onAutoRefresh, onRefresh, updatedAt, showBucket = true }) {
  return (
    <div className="toolbar">
      <div className="segmented" role="group" aria-label="Time range">
        {RANGE_PRESETS.map((p) => (
          <button key={p.id} type="button" className={range === p.id ? "active" : ""} aria-pressed={range === p.id} onClick={() => onRange(p.id)}>
            {p.label}
          </button>
        ))}
      </div>

      {showBucket && (
        <label className="toolbar-field">
          <span>Group by</span>
          <select value={bucket} onChange={(e) => onBucket(Number(e.target.value))}>
            {BUCKETS.map((b) => (
              <option key={b.minutes} value={b.minutes}>
                {b.label}
              </option>
            ))}
          </select>
        </label>
      )}

      <div className="toolbar-right">
        <label className="toolbar-check">
          <input type="checkbox" checked={autoRefresh} onChange={(e) => onAutoRefresh(e.target.checked)} />
          Auto-refresh
        </label>
        <button type="button" className="btn btn-ghost" onClick={onRefresh}>
          Refresh
        </button>
        {updatedAt && <span className="toolbar-updated">Updated {updatedAt.toLocaleTimeString()}</span>}
      </div>
    </div>
  );
}
