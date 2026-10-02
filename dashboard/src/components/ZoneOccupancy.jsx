const ZONE_TYPE_LABELS = {
  entrance: "Entrance",
  display_section: "Display section",
  billing_counter: "Billing counter",
  lobby: "Lobby",
  teller_counter: "Teller counter",
  generic: "Zone",
};

export default function ZoneOccupancy({ zones, dwellByZone }) {
  if (!zones || zones.length === 0) {
    return <p className="empty">No zones configured for this camera.</p>;
  }

  return (
    <div className="zone-grid">
      {zones.map((zone) => {
        const dwell = dwellByZone[zone.zone_id];
        // Fills toward the zone's occupancy alert limit when one is set;
        // otherwise it is only a soft visual cue (full at 10 people).
        const capacity = zone.max_occupancy_alert || 10;
        const meterPct = Math.min(100, (zone.current_occupancy / capacity) * 100);
        return (
          <div className="zone-card" key={zone.zone_id}>
            <div className="zone-card-header">
              <span className="zone-card-name">{zone.zone_name}</span>
              <span className="zone-card-type">{ZONE_TYPE_LABELS[zone.zone_type] || zone.zone_type}</span>
            </div>
            <div className="zone-card-occupancy-row">
              <span className="zone-card-occupancy">{zone.current_occupancy}</span>
              <span className="zone-card-occupancy-label">
                currently inside{zone.max_occupancy_alert ? ` · limit ${zone.max_occupancy_alert}` : ""}
              </span>
            </div>
            <div className="zone-card-meter-track">
              <div className="zone-card-meter-fill" style={{ width: `${meterPct}%` }} />
            </div>
            {dwell && dwell.visit_count > 0 ? (
              <div className="zone-card-dwell">
                <span className="zone-card-dwell-item">
                  <span className="zone-card-dwell-value">{dwell.visit_count}</span>
                  <span className="zone-card-dwell-label">visits</span>
                </span>
                <span className="zone-card-dwell-item">
                  <span className="zone-card-dwell-value">{dwell.avg_dwell_seconds.toFixed(1)}s</span>
                  <span className="zone-card-dwell-label">avg dwell</span>
                </span>
                <span className="zone-card-dwell-item">
                  <span className="zone-card-dwell-value">{dwell.max_dwell_seconds.toFixed(1)}s</span>
                  <span className="zone-card-dwell-label">max dwell</span>
                </span>
              </div>
            ) : (
              <div className="zone-card-dwell">
                <span className="zone-card-dwell-label">No visits recorded yet</span>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
