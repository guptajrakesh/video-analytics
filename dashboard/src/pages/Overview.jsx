import { useCallback, useEffect, useRef, useState } from "react";
import { api, fetchWebhookInbox, WEBHOOK_INBOX_URL } from "../api";
import { rangeParams } from "../lib/range";
import TimeControls from "../components/TimeControls";
import FootfallChart from "../components/FootfallChart";
import ZoneOccupancy from "../components/ZoneOccupancy";
import Heatmap from "../components/Heatmap";
import AlertsList from "../components/AlertsList";
import WebhookInbox from "../components/WebhookInbox";

function Panel({ title, subtitle, children }) {
  return (
    <section className="panel">
      <div className="panel-header">
        <div className="panel-title-group">
          <h2>{title}</h2>
          {subtitle && <span className="panel-subtitle">{subtitle}</span>}
        </div>
      </div>
      {children}
    </section>
  );
}

function Tile({ label, value, swatch }) {
  return (
    <div className="stat-tile">
      <div className="stat-tile-label">{label}</div>
      <div className="stat-tile-value-row">
        {swatch && <span className="stat-tile-swatch" style={{ background: swatch }} />}
        <span className="stat-tile-value">{value}</span>
      </div>
    </div>
  );
}

export default function Overview({ camera, view }) {
  const { range, bucket, tick, setUpdatedAt } = view;
  const [data, setData] = useState(null);
  const [alertFilter, setAlertFilter] = useState("open");
  const [busyAlert, setBusyAlert] = useState(null);
  const [inbox, setInbox] = useState([]);
  const [inboxUnreachable, setInboxUnreachable] = useState(false);
  const [error, setError] = useState(null);
  const requestId = useRef(0);

  const load = useCallback(async () => {
    const id = ++requestId.current;
    const params = rangeParams(range);
    try {
      const [summary, timeseries, heatmap, occupancy, zones, alerts, openAlerts] = await Promise.all([
        api.footfallSummary(camera.id, params),
        api.footfallTimeseries(camera.id, bucket, params),
        api.heatmap(camera.id, params),
        api.zonesOccupancy(camera.id),
        api.listZones(camera.id),
        api.alerts(camera.id, { status: alertFilter, ...params }),
        api.alerts(camera.id, { status: "open", limit: 500, ...params }),
      ]);
      const dwellEntries = await Promise.all(occupancy.map((z) => api.zoneDwell(z.zone_id, params).then((d) => [z.zone_id, d]).catch(() => [z.zone_id, null])));
      if (id !== requestId.current) return; // a newer request superseded this one
      setData({ summary, timeseries, heatmap, occupancy, zones, alerts, openAlerts: openAlerts.length, dwellByZone: Object.fromEntries(dwellEntries) });
      setError(null);
      setUpdatedAt(new Date());
    } catch (e) {
      if (id === requestId.current) setError(e.message);
    }

    if (WEBHOOK_INBOX_URL) {
      fetchWebhookInbox()
        .then((m) => {
          setInbox(m);
          setInboxUnreachable(false);
        })
        .catch(() => setInboxUnreachable(true));
    }
  }, [camera.id, range, bucket, alertFilter, setUpdatedAt]);

  useEffect(() => {
    setData(null);
  }, [camera.id]);

  useEffect(() => {
    load();
  }, [load, tick]);

  const changeStatus = async (alertId, status) => {
    setBusyAlert(alertId);
    try {
      await api.setAlertStatus(alertId, status);
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyAlert(null);
    }
  };

  const dwellList = data ? Object.values(data.dwellByZone).filter(Boolean) : [];
  const visits = dwellList.reduce((sum, d) => sum + d.visit_count, 0);
  const avgDwell = visits ? dwellList.reduce((sum, d) => sum + d.avg_dwell_seconds * d.visit_count, 0) / visits : 0;

  return (
    <>
      <TimeControls {...view} />
      {error && <div className="error-banner">{error}</div>}

      <div className={data ? "" : "is-loading"}>
        <section className="kpi-row">
          <Tile label="Total entries" value={data ? data.summary.total_in : "–"} swatch="var(--series-1)" />
          <Tile label="Total exits" value={data ? data.summary.total_out : "–"} swatch="var(--series-2)" />
          <Tile label="Zone visits" value={data ? visits : "–"} />
          <Tile label="Average dwell" value={data ? `${avgDwell.toFixed(1)}s` : "–"} />
          <Tile label="Open alerts" value={data ? data.openAlerts : "–"} />
        </section>

        <Panel title="Footfall over time" subtitle="People entering and leaving, grouped by the interval you choose above">
          <FootfallChart data={data?.timeseries} bucketMinutes={bucket} />
        </Panel>

        <Panel title="Zone occupancy & dwell" subtitle="Who's where right now, and how long people stay in each zone">
          <ZoneOccupancy zones={data?.occupancy} dwellByZone={data?.dwellByZone || {}} />
        </Panel>

        <Panel title="Position heatmap" subtitle="Where people spend the most time, drawn over the camera view">
          <Heatmap
            heatmap={data?.heatmap}
            frameUrl={camera.has_reference_frame ? api.referenceFrameUrl(camera.id, camera.has_reference_frame) : null}
            zones={data?.zones || []}
            entranceLine={camera.entrance_line}
          />
        </Panel>

        <Panel title="Alerts" subtitle="Threshold breaches for wait time and occupancy. Repeats within a zone's cooldown are grouped into one alert.">
          <AlertsList alerts={data?.alerts} filter={alertFilter} onFilter={setAlertFilter} onStatus={changeStatus} busyId={busyAlert} />
        </Panel>

        {WEBHOOK_INBOX_URL && (
          <Panel title="Webhook inbox" subtitle="What the alert webhook received — the same messages a Slack or Teams channel would show (demo receiver)">
            <WebhookInbox messages={inbox} cameraId={camera.id} unreachable={inboxUnreachable} />
          </Panel>
        )}
      </div>
    </>
  );
}
