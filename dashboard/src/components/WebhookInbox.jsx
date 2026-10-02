const TYPE_LABELS = {
  queue_wait_exceeded: "Wait time alert",
  occupancy_exceeded: "Overcrowding alert",
};

export default function WebhookInbox({ messages, cameraId, unreachable }) {
  if (unreachable) {
    return <p className="empty">The webhook receiver isn't reachable, so nothing can be shown here.</p>;
  }
  const mine = (messages || []).filter((m) => m.camera_id === cameraId);
  if (mine.length === 0) {
    return <p className="empty">No alerts have been delivered to the webhook for this camera.</p>;
  }

  return (
    <ul className="inbox-list">
      {mine.map((m, i) => (
        <li className="inbox-msg" key={i}>
          <span className="inbox-avatar" aria-hidden="true">
            VA
          </span>
          <div className="inbox-body">
            <div className="inbox-head">
              <span className="inbox-sender">Video Analytics</span>
              <span className="inbox-app">APP</span>
            </div>
            <div className="inbox-title">{TYPE_LABELS[m.alert_type] || m.alert_type}</div>
            <div className="inbox-text">
              {m.message} <span className="inbox-zone">· zone {m.zone_id}</span>
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}
