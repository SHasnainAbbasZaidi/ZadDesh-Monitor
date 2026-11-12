import { useEffect, useState } from 'react';
import { CircleAlert, Info, TriangleAlert } from 'lucide-react';
import { Badge } from '../components/ui/Badge';
import { Card } from '../components/ui/Card';
import { EntryDetails, incidentTitle, logTitle } from '../components/ui/EntryDetails';
import { MetricChart } from '../components/ui/MetricChart';
import { Modal } from '../components/ui/Modal';
import { useAuth } from '../context/AuthContext';
import { api, request, logSeverities, type InfrastructureLog } from '../services/api';
import { useAddonUpdates } from '../services/addonUpdates';
import type { Device, Site } from '../types';

type Tab = 'critical' | 'alerts' | 'history' | 'events' | 'errors' | 'logs';
const views: [Tab, string][] = [['critical', 'Critical Errors'], ['alerts', 'alerts'], ['history', 'Alert history'],
  ['events', 'events'], ['errors', 'errors'], ['logs', 'logs']];
type Filters = Record<string, string>;
type Row = {
  id: number; title: string; severity: string; status: string; suppression?: string;
  started_at: string; resolved_at?: string; duration_seconds: number; occurrences: number;
  flapping: boolean; device: string; device_id: number; check?: string; site?: string;
  category: 'device_offline' | 'link_down' | 'other';
};
type Event = { id: number; device_id?: number; title: string; message?: string; timestamp: string; severity: string; type: string; details: object };
type Detail = Row & {
  message?: string; metric?: string; samples: { timestamp: string; value: number }[];
  timeline: { id: number; kind: string; timestamp: string; message: string; actor?: string }[];
  events: Event[]; logs: object[]; logs_available: boolean; maintenance: { name: string; start: string; end: string }[];
  notifications: { id: number; channel: string; event: string; status: string; outcome?: string; timestamp: string }[];
};
type Saved = { id: number; name: string; values: Filters };

function queryString(filters: Filters) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) if (value) {
    query.set(key, key === 'start' || key === 'end' ? new Date(value).toISOString() : value);
  }
  return query.toString();
}

function duration(seconds: number) {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds / 60) % 60}m`;
}

function Raw({ value }: { value: object }) {
  const [copied, setCopied] = useState(false);
  const text = JSON.stringify(value, null, 2);
  return <details><summary className="cursor-pointer">View raw JSON</summary>
    <pre className="whitespace-pre-wrap break-all text-xs">{text}</pre>
    <button type="button" className="ops-button" onClick={() => {
      void navigator.clipboard.writeText(text).then(() => setCopied(true)).catch(() => setCopied(false));
    }}>{copied ? 'Copied' : 'Copy'}</button>
  </details>;
}

export function IncidentWorkspace({ initialTab = 'critical' }: { initialTab?: Tab }) {
  const { user } = useAuth();
  const admin = user?.role === 'admin';
  const [tab, setTab] = useState<Tab>(initialTab);
  const eventView = tab === 'events' || tab === 'errors';
  const alertView = tab === 'critical' || tab === 'alerts' || tab === 'history';
  const currentAlerts = alertView && tab !== 'history';
  const [draft, setDraft] = useState<Filters>({});
  const [filters, setFilters] = useState<Filters>({});
  const [alerts, setAlerts] = useState<Row[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [logs, setLogs] = useState<InfrastructureLog[]>([]);
  const [saved, setSaved] = useState<Saved[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [sites, setSites] = useState<Site[]>([]);
  const [offline, setOffline] = useState<string[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [expanded, setExpanded] = useState<number>();
  const [detail, setDetail] = useState<Detail>();
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [comment, setComment] = useState('');
  const [minutes, setMinutes] = useState(15);
  const [filterName, setFilterName] = useState('');
  const [utc, setUtc] = useState(false);
  const [paused, setPaused] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [offset, setOffset] = useState(0);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [removeOpen, setRemoveOpen] = useState(false);
  const [removeStart, setRemoveStart] = useState('');
  const [removeEnd, setRemoveEnd] = useState('');
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState('');
  const [notice, setNotice] = useState('');
  const [mobile, setMobile] = useState(() => window.matchMedia('(max-width: 767px)').matches);
  const time = (value: string) => new Date(value).toLocaleString(undefined, utc ? { timeZone: 'UTC', timeZoneName: 'short' } : {});
  useAddonUpdates(() => setRefresh(value => value + 1), paused);

  useEffect(() => {
    const media = window.matchMedia('(max-width: 767px)');
    const change = () => setMobile(media.matches);
    media.addEventListener('change', change);
    return () => media.removeEventListener('change', change);
  }, []);

  useEffect(() => {
    void api.getDevices().then(setDevices).catch(() => setDevices([]));
    void api.getSites().then(setSites).catch(() => setSites([]));
    void request<Saved[]>('/incidents/filters').then(setSaved).catch(() => setSaved([]));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const query = queryString(filters);
    async function load() {
      try {
        if (alertView) {
          const params = new URLSearchParams(query);
          params.set('offset', String(offset));
          params.set('view', tab === 'history' ? 'history' : 'current');
          params.set('hide_connection_noise', 'true');
          if (tab === 'critical') params.set('category', filters.category || 'critical');
          const result = await request<{ alerts: Row[]; has_more: boolean; offline_collectors: string[] }>(
            `/incidents/alerts?${params}`, { signal: controller.signal });
          if (controller.signal.aborted) return;
          setAlerts(result.alerts); setMore(result.has_more); setOffline(result.offline_collectors);
        } else if (eventView) {
          const result = await request<{ events: Event[]; has_more: boolean }>(
            `/incidents/events?offset=${offset}&hide_connection_noise=true&errors_only=${tab === 'errors'}&${query}`, { signal: controller.signal });
          if (controller.signal.aborted) return;
          setEvents(result.events); setMore(result.has_more);
        } else {
          setLogs(await request<InfrastructureLog[]>(`/logs?${query}`, { signal: controller.signal }));
        }
        setLoadError('');
      } catch (failure) {
        if (!controller.signal.aborted) setLoadError((failure as Error).message);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [alertView, eventView, filters, offset, refresh, tab]);

  useEffect(() => {
    if (expanded === undefined) return;
    const controller = new AbortController();
    void request<Detail>(`/incidents/alerts/${expanded}`, { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) { setDetail(result); setDetailError(''); }
    }).catch(failure => {
      if (!controller.signal.aborted) setDetailError(failure.message);
    });
    return () => controller.abort();
  }, [expanded, refresh]);

  function field(name: string, label: string, options?: [string, string][], type = 'text') {
    return <label className="text-xs" key={name}>{label}
      {options ? <select className="dash-select block" value={draft[name] ?? ''} onChange={e => setDraft({ ...draft, [name]: e.target.value })}>
        <option value="">Any</option>{options.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
      </select> : <input className="dash-select block" maxLength={120} type={type} value={draft[name] ?? ''} onChange={e => setDraft({ ...draft, [name]: e.target.value })} />}
    </label>;
  }

  async function action(kind: string, ids: number[]) {
    try {
      await request('/incidents/actions', { method: 'POST', body: JSON.stringify({ ids, action: kind, comment, minutes }) });
      setSelected([]); setComment(''); setRefresh(value => value + 1);
    } catch (failure) { setError((failure as Error).message); }
  }

  async function removeAlerts() {
    if (!removeStart || !removeEnd || new Date(removeEnd) < new Date(removeStart)) {
      setRemoveError('Choose a start time and an end time in chronological order.');
      return;
    }
    setRemoving(true); setRemoveError('');
    try {
      const result = await request<{ removed: number }>('/incidents/alerts/dismiss', {
        method: 'POST', body: JSON.stringify({ start: new Date(removeStart).toISOString(), end: new Date(removeEnd).toISOString() }),
      });
      setRemoveOpen(false); setRemoveStart(''); setRemoveEnd('');
      setSelected([]); setExpanded(undefined); setOffset(0); setRefresh(value => value + 1);
      setNotice(`${result.removed} ${result.removed === 1 ? 'alert' : 'alerts'} removed from the workspace.`);
    } catch (failure) {
      setRemoveError((failure as Error).message);
    } finally {
      setRemoving(false);
    }
  }

  function saveLogs() {
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([JSON.stringify(logs, null, 2)], { type: 'application/json' }));
    link.download = 'zaddesh-logs.json';
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  return <section className="space-y-4">
    <h1 className="ops-heading">Alerts, events and logs</h1>
    <div className="flex flex-wrap gap-2" aria-label="Incident views">
      {views.map(([view, label]) => <button className="ops-button capitalize" key={view} aria-pressed={tab === view} onClick={() => {
        if (tab === view) return;
        setTab(view); setDraft({}); setFilters({}); setOffset(0); setExpanded(undefined); setSelected([]); setLoading(true); setLoadError(''); setError('');
      }}>{label}</button>)}
      <button className="ops-button" aria-pressed={utc} onClick={() => setUtc(!utc)}>{utc ? 'UTC' : 'Local time'}</button>
      <button className="ops-button" onClick={() => setPaused(!paused)}>{paused ? 'Resume live updates' : 'Pause live updates'}</button>
      <button className="ops-button" onClick={() => setRefresh(value => value + 1)}>Refresh</button>
    </div>
    {error && <p role="alert" className="ops-notice">{error}</p>}
    {notice && <p role="status" className="ops-notice">{notice}</p>}
    {loadError && <p role="alert" className="ops-notice">Could not load {tab}. {loadError}</p>}
    {tab === 'alerts' && offline.map(name => <p className="ops-notice" key={name}>Collector offline · {name}</p>)}
    {currentAlerts && <p className="ops-muted text-sm">Alerts leave this view when they resolve or reach eight hours.</p>}
    <Card>
      <form className="flex flex-wrap items-end gap-3" onSubmit={event => {
        event.preventDefault(); setFilters(draft); setOffset(0); setSelected([]); setRefresh(value => value + 1);
      }}>
        {!eventView && field('q', 'Search')}
        {tab !== 'critical' && field('severity', 'Severity', (tab === 'errors' ? ['emergency', 'alert', 'critical', 'error', 'high']
          : tab === 'logs' ? [...logSeverities] : [...logSeverities, 'high', 'average']).map(level => [level, level]))}
        {tab === 'critical' && field('category', 'Category', [['device_offline', 'Device offline'], ['link_down', 'Uplink or downlink down']])}
        {tab === 'logs' ? field('source', 'Source') : field('device_id', 'Device', devices.map(device => [String(device.id), device.name]))}
        {eventView && field('type', 'Event type')}
        {alertView && <>
          {field('status', 'Status', [['active', 'Open'], ['acknowledged', 'Acknowledged'],
            ...(tab === 'history' ? [['resolved', 'Resolved']] as [string, string][] : []), ['suppressed', 'Suppressed']])}
          {field('site_id', 'Site', sites.map(site => [String(site.id), site.name]))}
          {tab !== 'critical' && field('type', 'Alert type', [['availability', 'Availability'], ['link', 'Link'], ['rule', 'Rule']])}
          {field('sort', 'Sort', [['newest', 'Newest first'], ['oldest', 'Oldest first']])}
        </>}
        {field('start', 'From', undefined, 'datetime-local')}{field('end', 'To', undefined, 'datetime-local')}
        <button className="ops-button" type="submit">Apply filters</button>
        {alertView && admin && <button className="ops-button" type="button" onClick={() => { setRemoveError(''); setRemoveOpen(true); }}>Remove alerts by date</button>}
        {tab === 'logs' && <button type="button" className="ops-button" onClick={saveLogs} disabled={!logs.length}>Download JSON</button>}
      </form>
      {tab === 'alerts' && <div className="mt-3 flex flex-wrap gap-2">
        <input className="dash-select" aria-label="Saved filter name" value={filterName} maxLength={80} onChange={event => setFilterName(event.target.value)} />
        <button className="ops-button" disabled={!filterName.trim()} onClick={async () => {
          try {
            await request('/incidents/filters', { method: 'POST', body: JSON.stringify({ name: filterName.trim(), values: draft }) });
            setSaved(await request<Saved[]>('/incidents/filters')); setFilterName('');
          } catch (failure) { setError((failure as Error).message); }
        }}>Save filter</button>
        {saved.map(item => <span className="flex gap-1" key={item.id}><button className="ops-button" onClick={() => {
          setDraft(item.values); setFilters(item.values); setOffset(0);
        }}>{item.name}</button><button className="ops-button" aria-label={`Delete ${item.name} filter`} onClick={async () => {
          try { await request(`/incidents/filters/${item.id}`, { method: 'DELETE' }); setSaved(items => items.filter(other => other.id !== item.id)); }
          catch (failure) { setError((failure as Error).message); }
        }}>×</button></span>)}
      </div>}
    </Card>
    {removeOpen && <Modal isOpen title="Remove alerts by date" onClose={() => { if (!removing) setRemoveOpen(false); }}>
      <form className="space-y-3" onSubmit={event => { event.preventDefault(); void removeAlerts(); }}>
        <p className="text-sm">Removes alerts started in this period from the current and history views. Times are local.</p>
        <div className="flex flex-wrap gap-3">
          <label className="text-sm">Removal start<input className="dash-select block" type="datetime-local" required value={removeStart} onChange={event => setRemoveStart(event.target.value)} /></label>
          <label className="text-sm">Removal end<input className="dash-select block" type="datetime-local" required value={removeEnd} onChange={event => setRemoveEnd(event.target.value)} /></label>
        </div>
        {removeError && <p role="alert" className="ops-notice">{removeError}</p>}
        <button className="ops-button" disabled={removing} type="submit">{removing ? 'Removing…' : 'Remove alerts'}</button>
      </form>
    </Modal>}
    {loading && <p role="status">Loading {tab}…</p>}
    {currentAlerts && admin && !!selected.length && <div className="flex flex-wrap items-center gap-2">
      <input className="dash-select" aria-label="Bulk comment" value={comment} maxLength={500} onChange={event => setComment(event.target.value)} />
      <label>Silence minutes <input className="dash-select w-24" type="number" min={1} max={1440} value={minutes} onChange={e => setMinutes(Number(e.target.value))} /></label>
      {['acknowledge', 'resolve', 'comment', 'silence'].map(kind => <button className="ops-button capitalize" key={kind} onClick={() => void action(kind, selected)}>{kind} selected</button>)}
    </div>}
    {alertView && !loading && alerts.map(row => {
      const Icon = row.severity === 'critical' ? CircleAlert : row.severity === 'info' ? Info : TriangleAlert;
      return <Card key={row.id}><div className="flex flex-wrap items-center gap-3 text-sm">
        {currentAlerts && admin && <input type="checkbox" aria-label={`Select ${row.title}`} checked={selected.includes(row.id)} onChange={event => {
          setSelected(event.target.checked ? [...selected, row.id] : selected.filter(id => id !== row.id));
        }} />}
        <Icon size={16} aria-hidden="true" /><Badge status={row.severity} />
        <button className="font-semibold underline" aria-expanded={expanded === row.id} onClick={() => {
          setDetail(undefined); setDetailError(''); setExpanded(expanded === row.id ? undefined : row.id);
        }}>{row.category === 'device_offline' ? 'Device offline' : incidentTitle(row.title)} · {row.device}{row.check ? ` · ${row.check}` : ''}</button>
        <Badge status={row.status}>{row.suppression ? `Suppressed (${row.suppression})` : row.status === 'active' ? 'Open' : row.status}</Badge>
        {row.flapping && <Badge status="warning">Flapping</Badge>}
        <span>Started {time(row.started_at)}</span><span>{duration(row.duration_seconds)}</span><span>{row.occurrences} occurrences</span>
      </div>
        {expanded === row.id && (() => {
          const body = detail?.id === row.id ? <div className="mt-4 space-y-3 text-sm">
          <p>{detail.message ?? detail.title}</p>
          <p>{[detail.device, detail.check, detail.site].filter(Boolean).join(' · ')}</p>
          {detail.resolved_at && <p>Resolved {time(detail.resolved_at)} · {duration(detail.duration_seconds)}</p>}
          <h3 className="font-semibold">Timeline</h3>
          {detail.timeline.length ? <ol className="space-y-2">{detail.timeline.map(item => <li key={item.id}>
            <time>{time(item.timestamp)}</time> · {item.kind} · {item.actor ? item.actor + ' · ' : ''}{item.message}
          </li>)}</ol> : <p className="ops-muted">Started {time(detail.started_at)}. Earlier timeline entries are unavailable.</p>}
          {!!detail.samples.length && <MetricChart title={detail.metric ?? 'Incident samples'} unit={detail.metric === 'snmp.available' ? 'availability' : 'ms'} data={detail.samples}
            annotations={[{ timestamp: detail.started_at, label: 'Started' }, ...(detail.resolved_at ? [{ timestamp: detail.resolved_at, label: 'Resolved' }] : [])]} />}
          <h3 className="font-semibold">Related events and logs</h3>
          {!detail.logs_available && <p className="ops-muted">Log storage is unavailable.</p>}
          {[...detail.events, ...detail.logs].map((item, index) => <Raw key={index} value={item} />)}
          {!!detail.maintenance.length && <div><h3 className="font-semibold">Maintenance windows</h3>{detail.maintenance.map(window => <p key={window.start}>{window.name} · {time(window.start)} – {time(window.end)}</p>)}</div>}
          <h3 className="font-semibold">Notifications</h3>
          {detail.notifications.length ? <ul>{detail.notifications.map(item => <li key={item.id}>
            {item.channel} · {item.event} · {item.status} · {time(item.timestamp)}{item.outcome && ` · ${item.outcome}`}
          </li>)}</ul> : <p className="ops-muted">No notifications recorded.</p>}
          {currentAlerts && admin && <div className="flex flex-wrap gap-2">
            <input className="dash-select" aria-label="Incident comment" value={comment} maxLength={500} onChange={event => setComment(event.target.value)} />
            {['acknowledge', 'comment', 'resolve', 'silence'].map(kind => <button key={kind} className="ops-button capitalize" onClick={() => void action(kind, [row.id])}>{kind}{kind === 'silence' ? ` for ${minutes}m` : ''}</button>)}
          </div>}
          </div> : detailError ? <p role="alert">Could not load incident. {detailError}</p> : <p role="status">Loading incident…</p>;
          return mobile ? <Modal isOpen drawer title={incidentTitle(row.title)} onClose={() => setExpanded(undefined)}>{body}</Modal> : body;
        })()}
      </Card>;
    })}
    {eventView && !loading && events.map(row => <Card key={row.id}>
      <EntryDetails title={incidentTitle(row.title, row.message, true)}>
        <p className="text-sm">{row.title}{row.message && row.message !== row.title ? `\n${row.message}` : ''}</p><Raw value={row} />
      </EntryDetails>
      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs"><Badge status={row.severity} />
        <time>{time(row.timestamp)}</time>{devices.find(device => device.id === row.device_id)?.name}
      </div>
    </Card>)}
    {tab === 'logs' && !loading && logs.map(row => <Card key={row.event_id}>
      <EntryDetails title={logTitle(row.message)}><p className="text-sm">{row.message}</p><Raw value={row} /></EntryDetails>
      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs"><Badge status={row.severity} /> <span>{row.source} · {time(row.timestamp)}</span></div>
    </Card>)}
    {!loading && !loadError && (alertView ? !alerts.length : eventView ? !events.length : !logs.length) && <p className="ops-empty">
      {tab === 'critical' ? 'No current critical errors.' : tab === 'history' ? 'No alert history matches these filters.'
        : tab === 'logs' && !Object.values(filters).some(Boolean) ? 'No infrastructure logs have been received yet.' : `No ${tab} match these filters.`}
    </p>}
    {tab !== 'logs' && <div className="flex gap-2">
      <button className="ops-button" disabled={!offset} onClick={() => setOffset(offset - 50)}>Previous</button>
      <button className="ops-button" disabled={!more} onClick={() => setOffset(offset + 50)}>Next</button>
    </div>}
  </section>;
}
