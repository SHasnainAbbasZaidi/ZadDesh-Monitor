import { useEffect, useState } from 'react';
import { Activity, ArrowRight, Bell, CircleCheck, CircleHelp, CircleX, Clock3, Network, Pause, Play, RefreshCw, Server, TriangleAlert } from 'lucide-react';
import { Badge } from '../components/ui/Badge';
import { EntryDetails, incidentTitle } from '../components/ui/EntryDetails';
import { OverviewLatency } from '../components/ui/OverviewLatency';
import { api, request } from '../services/api';
import { useLiveUpdates } from '../services/live';
import type { DeviceStats, FleetOverview, IncidentPreviewSummary, Site, SystemEvent } from '../types';

interface Snapshot {
  events: SystemEvent[];
  sites: Site[];
  received: Date;
}

const health = [
  { key: 'up', label: 'Healthy', icon: CircleCheck },
  { key: 'warning', label: 'Degraded', icon: TriangleAlert },
  { key: 'down', label: 'Down', icon: CircleX },
  { key: 'unknown', label: 'No availability data', icon: CircleHelp },
  { key: 'unmanaged', label: 'Unmanaged', icon: Server },
] as const;

function FleetRing({ stats }: { stats?: DeviceStats }) {
  const total = stats?.total_devices ?? 0;
  let offset = 0;
  return (
    <div className="dash-ring" aria-hidden="true">
      <svg viewBox="0 0 120 120">
        <circle className="dash-ring-track" cx="60" cy="60" r="52" />
        {stats && total > 0 && health.map(({ key }) => {
          const length = 100 * stats[key] / total;
          const start = offset;
          offset += length;
          return length > 0 && <circle key={key} data-tone={key} className="dash-ring-segment"
            cx="60" cy="60" r="52" pathLength="100" transform="rotate(-90 60 60)"
            strokeDasharray={`${length} ${100 - length}`} strokeDashoffset={-start} />;
        })}
      </svg>
      <div><strong>{stats ? total.toLocaleString() : '—'}</strong><span>devices</span></div>
    </div>
  );
}

const timeFormat = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' });
function eventTime(value: string) {
  const time = new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : value + "Z");
  return Number.isNaN(time.getTime()) ? '—' : timeFormat.format(time);
}

export function Overview({ incidents, criticalOnly, onRefreshIncidents, onNavigateToDevices, onNavigateToAlerts }: {
  incidents?: IncidentPreviewSummary;
  criticalOnly: boolean;
  onRefreshIncidents: () => void;
  onNavigateToDevices?: () => void;
  onNavigateToAlerts: () => void;
}) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [failed, setFailed] = useState(false);
  const [paused, setPaused] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [fleetRefresh, setFleetRefresh] = useState(0);
  const [fleet, setFleet] = useState<FleetOverview & { received: Date }>();
  const [fleetFailed, setFleetFailed] = useState(false);
  const [pausedIncidents, setPausedIncidents] = useState<IncidentPreviewSummary>();
  const [minutes, setMinutes] = useState(60);
  const [siteFilter, setSiteFilter] = useState('all');
  const connection = useLiveUpdates(() => setRefresh(value => value + 1), paused);

  useEffect(() => {
    if (paused) return;
    let active = true;
    let timer: number | undefined;
    async function poll() {
      try {
        const [events, sites] = await Promise.all([api.getEvents(8), api.getSites()]);
        if (active) {
          setSnapshot({ events, sites, received: new Date() });
          setFailed(false);
        }
      } catch {
        if (active) setFailed(true);
      } finally {
        if (active) timer = window.setTimeout(poll, 10_000);
      }
    }
    void poll();
    return () => { active = false; window.clearTimeout(timer); };
  }, [paused, refresh]);

  useEffect(() => {
    if (paused) return;
    const controller = new AbortController();
    let timer: number | undefined;
    async function loadFleet() {
      try {
        const result = await request<FleetOverview>('/zabbix/fleet', { signal: controller.signal });
        if (!controller.signal.aborted) {
          setFleet(previous => result.zabbix_unavailable && previous && !previous.zabbix_unavailable
            ? previous : { ...result, received: new Date() });
          setFleetFailed(Boolean(result.zabbix_unavailable));
        }
      } catch {
        if (!controller.signal.aborted) setFleetFailed(true);
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(loadFleet, 30_000);
      }
    }
    void loadFleet();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [paused, fleetRefresh]);

  function refreshDashboard() {
    setRefresh(value => value + 1);
    setFleetRefresh(value => value + 1);
    onRefreshIncidents();
  }

  const incidentData = paused ? pausedIncidents : incidents;
  const incidentLabel = criticalOnly ? 'Critical incidents' : 'Active incidents';
  const stats = fleet?.stats;
  const total = stats?.total_devices ?? 0;
  const issue = stats ? stats.down + stats.warning : 0;
  const devices = fleet?.devices ?? [];
  const sites = new Map(snapshot?.sites.map(site => [site.id, site.name]));
  const visibleDevices = devices.filter(device => siteFilter === 'all' || String(device.site_id ?? 'unassigned') === siteFilter);
  const live = !paused && !failed && connection === 'connected';

  const statCards = [
    { label: 'Total devices', value: stats?.total_devices, icon: Server, tone: 'accent', hint: 'Combined inventory', action: onNavigateToDevices },
    { label: 'Healthy', value: stats?.up, icon: CircleCheck, tone: 'up', hint: 'Available devices', action: onNavigateToDevices },
    { label: 'Needs attention', value: stats ? issue : undefined, icon: TriangleAlert, tone: 'warning', hint: 'Down or degraded', action: onNavigateToDevices },
    { label: incidentLabel, value: incidentData?.total, icon: Bell, tone: 'down',
      hint: incidentData?.incomplete ? 'Some incident data is unavailable' : criticalOnly ? 'Current outages' : 'Unacknowledged alerts', action: onNavigateToAlerts },
  ];

  return (
    <div className="network-dashboard">
      <header className="dash-top">
        <div>
          <p className="dash-kicker"><Network size={14} /> Network monitoring</p>
          <h1>Infrastructure overview</h1>
          <p className="ops-muted">Device health, probe performance, and incidents.</p>
        </div>
        <div className="dash-toolbar">
          <span className="dash-live" data-live={live}>
            <i aria-hidden="true" />{paused ? 'Refresh paused' : failed ? 'API unavailable' : live ? 'Live updates connected' : snapshot ? 'Polling' : 'Connecting'}
          </span>
          <div className="dash-time"><Clock3 size={15} aria-hidden="true" />
            <select aria-label="Telemetry time range" value={minutes} onChange={event => setMinutes(Number(event.target.value))}>
              <option value={15}>Last 15 minutes</option><option value={60}>Last 1 hour</option>
              <option value={360}>Last 6 hours</option><option value={1440}>Last 24 hours</option>
            </select>
          </div>
          <button className="ops-button dash-icon-button" aria-label="Refresh dashboard" title="Refresh dashboard" disabled={paused} onClick={refreshDashboard}><RefreshCw size={15} /></button>
          <button className="ops-button dash-icon-button" aria-label={paused ? 'Resume refresh' : 'Pause refresh'} title={paused ? 'Resume refresh' : 'Pause refresh'} onClick={() => {
            if (!paused) setPausedIncidents(incidents);
            setPaused(!paused);
          }}>
            {paused ? <Play size={15} /> : <Pause size={15} />}
          </button>
        </div>
      </header>

      {failed && <div role="status" className="ops-notice">
        <TriangleAlert size={18} /><span>{snapshot ? 'Update failed. Showing the last received snapshot.' : 'Could not load infrastructure data.'}</span>
        <button className="ops-button" onClick={() => { setPaused(false); refreshDashboard(); }}>Retry</button>
      </div>}

      <section className="dash-stat-grid" aria-label="Infrastructure summary" aria-busy={!stats && !fleetFailed}>
        {statCards.map(({ label, value, icon: Icon, tone, hint, action }) => {
          const Item = action ? 'button' : 'div';
          return <Item type={action ? 'button' : undefined} className="dash-stat" data-tone={tone} key={label} onClick={action}>
            <span className="dash-stat-label"><span className="dash-stat-icon"><Icon size={16} /></span>{label}</span>
            <strong>{value?.toLocaleString() ?? '—'}</strong>
            <span className="dash-stat-foot">{hint}{action && <ArrowRight size={14} aria-hidden="true" />}</span>
          </Item>;
        })}
      </section>

      <div className="dash-grid">
        <OverviewLatency minutes={minutes} updatedAt={snapshot?.received.getTime()} />

        <section className="dash-panel" aria-labelledby="fleet-title">
          <header className="dash-panel-head"><div><h2 id="fleet-title"><Network size={16} /> Fleet health</h2><p>Current device status from ZadDesh and Zabbix</p></div></header>
          <div className="dash-panel-body">
            <div className="dash-fleet">
              <FleetRing stats={stats} />
              <dl className="dash-legend">
                {health.map(({ key, label, icon: Icon }) => <div key={key}>
                  <dt><Icon size={15} data-tone={key} />{label}</dt>
                  <dd>{stats?.[key].toLocaleString() ?? '—'}</dd>
                </div>)}
              </dl>
            </div>
            <div className="dash-fleet-foot" role="status">
              <h2>{!stats ? (fleetFailed ? 'Health unavailable' : 'Loading health') : !total ? 'No devices configured' : issue ? `${issue} ${issue === 1 ? 'needs' : 'need'} attention` : stats.up ? 'No reported degradation' : 'Awaiting device checks'}</h2>
              <p>{fleetFailed ? (fleet?.zabbix_unavailable ? 'Zabbix is unavailable. Showing local device status.'
                : fleet ? 'Fleet refresh failed. Showing the last received status.' : 'Could not load fleet status.')
                : fleet ? `Updated at ${eventTime(fleet.received.toISOString())}` : 'Waiting for the monitoring API'}</p>
            </div>
          </div>
        </section>

        <section className="dash-panel" aria-labelledby="devices-title">
          <header className="dash-panel-head">
            <div><h2 id="devices-title"><Server size={16} /> Device inventory</h2>
              <p>Last reported device status by site</p></div>
            {onNavigateToDevices && <button className="ops-button" onClick={onNavigateToDevices}>View devices <ArrowRight size={14} /></button>}
          </header>
          <div className="dash-panel-body">
            {devices.length > 0 && <div className="dash-inventory-controls">
              <select aria-label="Filter devices by site" value={siteFilter} className="dash-select" onChange={event => setSiteFilter(event.target.value)}>
                <option value="all">All sites</option>
                {snapshot?.sites.map(site => <option key={site.id} value={site.id}>{site.name}</option>)}
                <option value="unassigned">Unassigned</option>
              </select>
              <span className="ops-muted text-xs">{visibleDevices.length} shown</span>
            </div>}
            {visibleDevices.length ? <div className="dash-table-scroll"><table className="dash-table">
              <thead><tr><th scope="col">Device</th><th scope="col">Site</th><th scope="col">Status</th></tr></thead>
              <tbody>{visibleDevices.map(device => <tr key={device.id}>
                <td><strong>{device.name}</strong><span className="font-mono">{device.ip}</span></td>
                <td>{sites.get(device.site_id ?? -1) ?? 'Unassigned'}</td>
                <td><Badge status={device.status}>{device.status_label ?? (device.status === 'unknown' ? 'No availability data' : device.status)}</Badge></td>
              </tr>)}</tbody>
            </table></div> : <div className="dash-empty">
              <Server size={21} /><strong>{fleet ? devices.length ? 'No devices in this site' : 'Your inventory is empty'
                : fleetFailed ? 'Device inventory unavailable' : 'Waiting for device inventory'}</strong>
              {onNavigateToDevices && fleet && !devices.length && <button className="ops-button" onClick={onNavigateToDevices}>Open Devices</button>}
            </div>}
          </div>
        </section>

        <div className="dash-side-stack">
          <section className="dash-panel" aria-labelledby="incident-title">
            <header className="dash-panel-head"><div><h2 id="incident-title"><Bell size={16} /> {incidentLabel}</h2>
              <p>{incidentData ? `${incidentData.total} ${criticalOnly ? 'current' : 'unacknowledged'}${incidentData.entries.length < incidentData.total ? ` · Showing latest ${incidentData.entries.length}` : ''}` : 'Waiting for alert data'}</p></div>
              <button className="ops-button" onClick={onNavigateToAlerts}>Open alerts <ArrowRight size={14} /></button>
            </header>
            <div className="dash-panel-body">
              {incidentData?.entries.length ? <ul className="dash-feed">{incidentData.entries.map(alert => <li key={alert.id}>
                <EntryDetails title={`${alert.category === 'device_offline' ? 'Device offline' : incidentTitle(alert.title)}${alert.device ? ` · ${alert.device}` : ''}`}>
                  <div>{alert.title}</div>{alert.message && <div>{alert.message}</div>}
                </EntryDetails><Badge status={alert.severity} />
              </li>)}</ul> : <p className="dash-empty"><CircleCheck size={20} />{incidentData?.incomplete ? 'Incident data is incomplete'
                : incidentData ? (criticalOnly ? 'No current critical incidents' : 'No active incidents') : 'Waiting for incident data'}</p>}
            </div>
          </section>
          <section className="dash-panel" aria-labelledby="activity-title">
            <header className="dash-panel-head"><div><h2 id="activity-title"><Activity size={16} /> Recent activity</h2><p>Latest events recorded by the core</p></div></header>
            <div className="dash-panel-body">
              {snapshot?.events.length ? <ol className="dash-feed">{snapshot.events.map(event => <li key={event.id}>
                <div className="min-w-0"><EntryDetails title={incidentTitle(event.title, event.message, true)}>
                  <div>{event.title}</div>{event.message && <div>{event.message}</div>}
                  {Object.keys(event.details ?? {}).length > 0 && <pre className="whitespace-pre-wrap">{JSON.stringify(event.details, null, 2)}</pre>}
                </EntryDetails><p><time dateTime={event.timestamp}>{eventTime(event.timestamp)}</time></p></div><Badge status={event.severity} />
              </li>)}</ol> : <p className="dash-empty"><Clock3 size={20} />{snapshot ? 'No recent events' : 'Waiting for event data'}</p>}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
