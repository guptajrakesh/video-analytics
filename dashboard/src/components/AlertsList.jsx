const ALERT_META = {
  queue_wait_exceeded: { label: "Wait time", icon: "⏱", status: "warning" }, // stopwatch
  occupancy_exceeded: { label: "Overcrowding", icon: "⚠", status: "critical" }, // warning triangle
};

const FILTERS = [
  { id: "open", label: "Open" },
  { id: "acknowledged", label: "Acknowledged" },
  { id: "resolved", label: "Resolved" },
  { id: "", label: "All" },
];

function formatSpan(first, last) {
  const a = new Date(first);
  const b = new Date(last || first);
  const time = (d) => d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", second: "2-digit" });
  const day = a.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return b - a > 1000 ? `${day}, ${time(a)} – ${time(b)}` : `${day}, ${time(a)}`;
}

export default function AlertsList({ alerts, filter, onFilter, onStatus, busyId }) {
  return (
    <div>
      <div className="segmented segmented-small" role="group" aria-label="Alert status">
        {FILTERS.map((f) => (
          <button key={f.id || "all"} type="button" className={filter === f.id ? "active" : ""} aria-pressed={filter === f.id} onClick={() => onFilter(f.id)}>
            {f.label}
          </button>
        ))}
      </div>

      {(!alerts || alerts.length === 0) && <p className="empty">{filter === "open" ? "No open alerts — all clear." : "No alerts to show."}</p>}

      <ul className="alerts-list">
        {(alerts || []).map((a) => {
          const meta = ALERT_META[a.alert_type] || { label: a.alert_type, icon: "•", status: "warning" };
          return (
            <li className={`alert-item ${a.status !== "open" ? "alert-done" : ""}`} key={a.id}>
              <span className={`alert-icon ${meta.status}`} aria-hidden="true">
                {meta.icon}
              </span>
              <div className="alert-body">
                <div className="alert-label-row">
                  <span className="alert-type-label">{meta.label}</span>
                  {a.occurrences > 1 && <span className="alert-count">×{a.occurrences}</span>}
                  <span className="alert-time">{formatSpan(a.occurred_at, a.last_occurred_at)}</span>
                </div>
                <div className="alert-message">{a.message}</div>
              </div>
              <div className="alert-side">
                <div className="alert-badges">
                  <span className={`alert-badge ${a.delivered ? "delivered" : "pending"}`}>{a.delivered ? "Delivered" : "Not delivered"}</span>
                  {a.status !== "open" && <span className="alert-badge state">{a.status === "acknowledged" ? "Acknowledged" : "Resolved"}</span>}
                </div>
                <div className="alert-actions">
                  {a.status === "open" && (
                    <button type="button" className="btn btn-small" disabled={busyId === a.id} onClick={() => onStatus(a.id, "acknowledged")}>
                      Acknowledge
                    </button>
                  )}
                  {a.status !== "resolved" && (
                    <button type="button" className="btn btn-small" disabled={busyId === a.id} onClick={() => onStatus(a.id, "resolved")}>
                      Resolve
                    </button>
                  )}
                  {a.status !== "open" && (
                    <button type="button" className="btn btn-small btn-ghost" disabled={busyId === a.id} onClick={() => onStatus(a.id, "open")}>
                      Reopen
                    </button>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
