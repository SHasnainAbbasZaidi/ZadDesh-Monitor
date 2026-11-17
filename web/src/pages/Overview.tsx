import { useEffect, useState } from 'react';
import { Activity, ArrowRight, Bell, CircleCheck, Clock3, TriangleAlert } from 'lucide-react';
import { Badge } from '../components/ui/Badge';
import { EntryDetails, incidentTitle } from '../components/ui/EntryDetails';
import { OverviewLatency } from '../components/ui/OverviewLatency';
import { InfrastructureMonitoring } from '../components/ui/InfrastructureMonitoring';
import { OverviewDonuts, OverviewTiles } from '../components/ui/OverviewSummary';
import { api, request } from '../services/api';
import { useLiveUpdates } from '../services/live';
import type { FleetOverview, IncidentPreviewSummary, Site, SystemEvent } from '../types';

interface Snapshot {
  events: SystemEvent[];
  sites: Site[];
  received: Date;
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
  const [refresh, setRefresh] = useState(0);
  const [fleetRefresh, setFleetRefresh] = useState(0);
  const [fleet, setFleet] = useState<FleetOverview>();
  const [fleetFailed, setFleetFailed] = useState(false);
  const [minutes, setMinutes] = useState(60);
  const connection = useLiveUpdates(() => setRefresh(value => value + 1));

  useEffect(() => {
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
  }, [refresh]);

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    async function loadFleet() {
      try {
        const result = await request<FleetOverview>('/zabbix/fleet', { signal: controller.signal });
        if (!controller.signal.aborted) {
          setFleet(previous => result.zabbix_unavailable && previous && !previous.zabbix_unavailable
            ? previous : result);
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
  }, [fleetRefresh]);

  function refreshDashboard() {
    setRefresh(value => value + 1);
    setFleetRefresh(value => value + 1);
    onRefreshIncidents();
  }

  const incidentData = incidents;
  const incidentLabel = criticalOnly ? 'Critical incidents' : 'Active incidents';
  const live = !failed && connection === 'connected';

  return (
    <div className="network-dashboard">
      <header className="dash-top">
        <div>
          <h1>Infrastructure overview</h1>
        </div>
        <div className="dash-toolbar">
          <span className="dash-live" data-live={live}>
            <i aria-hidden="true" />{failed ? 'API unavailable' : live ? 'Live updates connected' : snapshot ? 'Polling' : 'Connecting'}
          </span>
          <div className="dash-time"><Clock3 size={15} aria-hidden="true" />
            <select aria-label="Telemetry time range" value={minutes} onChange={event => setMinutes(Number(event.target.value))}>
              <option value={15}>Last 15 minutes</option><option value={60}>Last 1 hour</option>
              <option value={360}>Last 6 hours</option><option value={1440}>Last 24 hours</option>
            </select>
          </div>
        </div>
      </header>

      {failed && <div role="status" className="ops-notice">
        <TriangleAlert size={18} /><span>{snapshot ? 'Update failed. Showing the last received snapshot.' : 'Could not load infrastructure data.'}</span>
        <button className="ops-button" onClick={refreshDashboard}>Retry</button>
      </div>}

      <div className="overview-top-grid">
        <OverviewTiles stats={fleet?.stats} incidents={incidentData} incidentLabel={incidentLabel} fleetFailed={fleetFailed}
          onDevices={onNavigateToDevices} onAlerts={onNavigateToAlerts} />
        <OverviewLatency minutes={minutes} updatedAt={snapshot?.received.getTime()} />
      </div>
      <OverviewDonuts fleet={fleet} fleetFailed={fleetFailed} incidents={incidentData} />

      <div className="dash-grid">
        <InfrastructureMonitoring fleet={fleet} sites={snapshot?.sites} refresh={fleetRefresh} onNavigate={onNavigateToDevices} />

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
