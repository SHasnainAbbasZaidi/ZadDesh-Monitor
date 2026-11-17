import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { ArrowRight, ChevronDown, Server } from 'lucide-react';
import { Badge } from './Badge';
import { MetricChart, type MetricPoint } from './MetricChart';
import { Modal } from './Modal';
import { useAuth } from '../../context/AuthContext';
import { request } from '../../services/api';
import type { FleetOverview, Site } from '../../types';
import './infrastructureMonitoring.css';

const categories = ['Network', 'Servers', 'Routers', 'Firewalls', 'CCTV', 'Other Devices'] as const;
const resourceKeys = ['cpu', 'memory', 'network'] as const;
const resourceLabel = { cpu: 'CPU', memory: 'Memory', network: 'Network' };
const rangeChoices = [['1', 'Last hour'], ['6', 'Last 6 hours'], ['24', 'Last 24 hours'], ['168', 'Last 7 days'], ['720', 'Last 30 days']];
type Category = typeof categories[number];
type Resource = typeof resourceKeys[number];
type Reading = { current: number; value: number; samples: number };
type Problem = { id: string; title: string; severity: number; suppressed: boolean };
type Device = {
  id: string; host_id?: string; name: string; ip: string; category: Category;
  status: string; severity: number; resources: Partial<Record<Resource, Reading>>; health: number | null;
  penalties: { factor: string; reason: string; points: number }[];
  factors: { interfaces: boolean; hardware: boolean; performance: boolean };
  critical_count: number; problems_known: boolean; problem_count: number; last_seen: string | null;
};
type Config = { weights: Record<Category, Record<Resource, number>>; network_policy: string };
type Summary = { devices: Device[]; config: Config; at: string | null; refresh_seconds: number };
type Series = { name: string; unit: string; current: number | null; min: number; max: number; avg: number; points: MetricPoint[]; interval_seconds?: number | null };
type Port = { id: string; name: string; in: number | null; out: number | null; speed: number | null; status: number | null;
  adminstatus: number | null; errors: number | null; discards: number | null; utilization: number | null; uplink: boolean;
  rx_utilization: number | null; tx_utilization: number | null };
type Detail = { device: Device; series: Series[]; trends: boolean; interfaces: Port[]; problems: Problem[];
  sensors: { name: string; value: string; unit: string }[]; uptime: number | null; vendor: string; model: string;
  memory_available: number | null; memory_total: number | null };

const defaults = { cpu: 40, memory: 30, network: 30 };
export function usageScore(resources: Device['resources'], weights: Record<Resource, number>) {
  const available = resourceKeys.filter(key => resources[key] !== undefined && weights[key] > 0);
  const total = available.reduce((sum, key) => sum + weights[key], 0);
  const score = total ? available.reduce((sum, key) => sum + resources[key]!.value * weights[key], 0) / total : null;
  return { score, available, total };
}

function usageTone(value: number | null) {
  return value === null ? 'unknown' : value < 50 ? 'up' : value < 70 ? 'warning' : value < 85 ? 'orange' : 'down';
}
function healthTone(value: number | null) {
  return value === null ? 'unknown' : value >= 90 ? 'up' : value >= 75 ? 'warning' : value >= 50 ? 'orange' : 'down';
}
function statusFor(device: Device) {
  if (device.status === 'offline' || device.severity >= 4) return ['down', device.status === 'offline' ? 'Offline' : 'Critical'];
  if (device.status === 'maintenance') return ['unknown', 'Maintenance'];
  if (device.status === 'warning' || device.severity >= 2) return ['warning', 'Warning'];
  if (device.status === 'online') return ['up', 'Online'];
  return ['unknown', device.status === 'disabled' ? 'Disabled' : 'Unknown'];
}
const pct = (value: number | null | undefined) => value == null ? 'N/A' : `${Math.round(value)}%`;
const formatted = (value: number | null | undefined, unit = '') => {
  if (value == null) return 'N/A';
  if (unit === 'bps') {
    const divisor = value >= 1e9 ? 1e9 : value >= 1e6 ? 1e6 : value >= 1e3 ? 1e3 : 1;
    return `${(value / divisor).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${divisor === 1e9 ? 'Gbps' : divisor === 1e6 ? 'Mbps' : divisor === 1e3 ? 'Kbps' : 'bps'}`;
  }
  if (unit === 'B' && value >= 1024) {
    const exponent = Math.min(4, Math.floor(Math.log(value) / Math.log(1024)));
    return `${(value / 1024 ** exponent).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${['B', 'KiB', 'MiB', 'GiB', 'TiB'][exponent]}`;
  }
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}${unit ? ` ${unit}` : ''}`;
};

function ResourceMeter({ name, value }: { name: string; value: number | null }) {
  return <div className="infra-meter" data-level={usageTone(value)}>
    <span>{name}</span><strong>{pct(value)}</strong>
    <span className="infra-meter-track" aria-hidden="true"><i style={{ width: `${value ?? 0}%` }} /></span>
  </div>;
}

function Breakdown({ device, weights }: { device: Device; weights: Record<Resource, number> }) {
  const usage = usageScore(device.resources, weights);
  return <div className="infra-breakdown">
    <div>
      {resourceKeys.map(key => <ResourceMeter key={key} name={resourceLabel[key]} value={device.resources[key]?.value ?? null} />)}
      <p className="mt-2"><strong>Overall usage {pct(usage.score)}</strong> <span className="ops-muted">· {usage.available.length}/3 resources</span></p>
    </div>
    <dl className="space-y-1">
      <div><dt>Connectivity</dt><dd>{device.status}</dd></div>
      {Object.entries(device.factors).map(([key, known]) => <div key={key} className="capitalize"><dt>{key}</dt>
        <dd>{device.penalties.some(penalty => (key === 'interfaces' && ['uplink', 'errors'].includes(penalty.factor)) ||
          (key === 'hardware' && penalty.factor === 'hardware') || (key === 'performance' && penalty.factor === 'pressure'))
          ? 'Issue detected' : known ? 'Monitored' : 'No data'}</dd></div>)}
      <div><dt>Critical alerts</dt><dd>{device.problems_known ? device.critical_count : 'N/A'}</dd></div>
      <div><dt>Overall device health</dt><dd><strong>{pct(device.health)}</strong></dd></div>
      <div><dt>Health coverage</dt><dd>{Object.values(device.factors).filter(Boolean).length}/3 factors</dd></div>
      {device.penalties.length > 0 && <div><dt className="ops-muted">Largest penalty</dt><dd>−{Math.max(...device.penalties.map(item => item.points))} pts</dd></div>}
    </dl>
  </div>;
}

function ChartSeries({ series }: { series: Series[] }) {
  if (!series.length) return <p className="ops-muted">Zabbix returned no samples for this range.</p>;
  return <div className="infra-detail-grid">{series.map(item => <div key={item.name} className="dash-panel p-3 min-w-0">
    <p className="ops-muted text-xs">Current {formatted(item.current, item.unit)} · Min {formatted(item.min, item.unit)} · Avg {formatted(item.avg, item.unit)} · Max {formatted(item.max, item.unit)}</p>
    <MetricChart title={item.name.replace(/^cpu$/, 'CPU utilization').replace(/^memory$/, 'Memory utilization')} unit={item.unit} data={item.points} height={155}
      intervalSeconds={item.interval_seconds ?? undefined} />
  </div>)}</div>;
}

function PortHistory({ device, port, range }: { device: Device; port: Port; range: string }) {
  const [series, setSeries] = useState<Series[]>();
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    request<{ series: Series[] }>(`/infrastructure-monitoring/${device.host_id}?hours=${range}&interface=${port.id}`, { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) setSeries(data.series); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [device.host_id, port.id, range]);
  if (error) return <p role="alert">Could not load interface history.</p>;
  return series ? <ChartSeries series={series} /> : <p role="status">Loading interface history…</p>;
}

function DevicePanel({ device, weights, policy, category, onCategory, onClose, canReadDetails }: {
  device: Device; weights: Record<Resource, number>; policy?: string; category: Category | ''; onCategory: (category: Category | '') => void; onClose: () => void; canReadDetails: boolean;
}) {
  const [range, setRange] = useState('1');
  const [details, setDetails] = useState<Detail>();
  const [openPort, setOpenPort] = useState<string>();
  const [error, setError] = useState(false);
  useEffect(() => {
    if (!device.host_id || !canReadDetails) return;
    const controller = new AbortController();
    setError(false);
    setDetails(undefined);
    request<Detail>(`/infrastructure-monitoring/${device.host_id}?hours=${range}`, { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) setDetails(data); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [device.host_id, range, canReadDetails]);
  const usage = usageScore(device.resources, weights);
  return <Modal isOpen wide title={device.name} onClose={onClose}>
    <div className="infra-device-content space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><p className="font-mono">{device.ip || 'No IP reported'}</p><p className="ops-muted text-sm">{device.host_id ? `Zabbix host ${device.host_id}` : 'ZadDesh inventory'} · {statusFor(device)[1]}</p></div>
        <label className="text-sm">Category<select className="dash-select block" value={category} onChange={event => onCategory(event.target.value as Category | '')}>
          <option value="">Automatic</option>{categories.map(value => <option key={value}>{value}</option>)}
        </select></label>
      </div>
      <dl className="infra-detail-grid text-sm">
        <div><dt>Monitoring category</dt><dd>{device.category}{category && ' · Override'}</dd></div>
        <div><dt>Manufacturer and model</dt><dd>{[details?.vendor, details?.model].filter(Boolean).join(' · ') || 'N/A'}</dd></div>
        <div><dt>Uptime</dt><dd>{details?.uptime == null ? 'N/A' : `${Math.floor(details.uptime / 86400)}d ${Math.floor(details.uptime / 3600) % 24}h ${Math.floor(details.uptime / 60) % 60}m`}</dd></div>
        <div><dt>Latest monitoring sample</dt><dd>{device.last_seen ? new Date(device.last_seen).toLocaleString() : 'N/A'}</dd></div>
      </dl>
      <section className="dash-panel p-3"><h3 className="font-semibold mb-2">Resource and health breakdown</h3><Breakdown device={device} weights={weights} />
        <p className="mt-3 text-xs ops-muted">Usage uses the latest 15-minute averages where available and the latest fresh sample otherwise. Network uses the busiest of up to three {policy === 'uplinks' ? 'uplinks' : 'active interfaces'} and the busier direction.</p>
        <ul className="text-sm mt-2">{resourceKeys.map(key => <li key={key}>{resourceLabel[key]} {pct(device.resources[key]?.value)} · {usage.available.includes(key) ? `${Math.round(weights[key] * 100 / usage.total)}% applied weight` : 'excluded'}</li>)}</ul>
        <p className="mt-3 text-xs ops-muted">Health uses confirmed conditions and current monitoring coverage. Unknown factors stay unscored.</p>
        {device.penalties.length > 0 && <ul className="text-sm mt-1 space-y-1">{device.penalties.map(item => <li key={item.factor}>−{item.points} · {item.reason}</li>)}</ul>}
      </section>
      {!device.host_id ? <p className="ops-muted text-sm">No linked Zabbix host is available for this device.</p> : !canReadDetails ?
        <p className="ops-muted text-sm">An administrator can open raw Zabbix history and interface details.</p> : <>
        <section className="space-y-3">
          <div className="flex justify-between items-end gap-2 flex-wrap"><h3 className="font-semibold">Resource history</h3>
            <label className="text-sm">History range<select className="dash-select block" value={range} onChange={event => setRange(event.target.value)}>{rangeChoices.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          </div>
          {error ? <p role="alert" className="ops-notice">Could not load Zabbix details for this range.</p> : details ? <>
            {(details.memory_available != null || details.memory_total != null) && <p className="ops-muted text-xs">Memory available {formatted(details.memory_available, 'B')} · Total {formatted(details.memory_total, 'B')}</p>}
            {details.trends && <p className="ops-muted text-xs">Hourly Zabbix trends</p>}<ChartSeries series={details.series} />
          </> : <p role="status" className="ops-muted">Loading device history…</p>}
        </section>
        {details && <>
          <section className="space-y-2"><h3 className="font-semibold">Interfaces</h3>
            {details.interfaces.length === 0 && <p className="ops-muted">No interface measurements available.</p>}
            {details.interfaces.length > 0 && <p className="text-xs ops-muted">Across monitored interfaces · RX {formatted(totalTraffic(details.interfaces, 'in'), 'bps')} · TX {formatted(totalTraffic(details.interfaces, 'out'), 'bps')}</p>}
            {[...details.interfaces].sort((a, b) => (b.utilization ?? -1) - (a.utilization ?? -1)).map(port => <div key={port.id} className="dash-panel p-3">
              <button type="button" className="flex flex-wrap items-center gap-2 w-full text-left text-sm" aria-expanded={openPort === port.id} onClick={() => setOpenPort(previous => previous === port.id ? undefined : port.id)}>
                <ChevronDown size={14} aria-hidden="true" /><strong>{port.name}</strong>{port.uplink && <Badge status="info">Uplink</Badge>}
                <span className="ops-muted">{port.adminstatus === 2 ? 'Disabled' : port.status === 1 ? 'Up' : port.status === 2 || port.status === 7 ? 'Down' : 'Unknown'} · {formatted(port.speed, 'bps')} · {pct(port.utilization)} utilized</span>
              </button>
              <p className="text-xs mt-2 ops-muted">RX {formatted(port.in, 'bps')} ({pct(port.rx_utilization)}) · TX {formatted(port.out, 'bps')} ({pct(port.tx_utilization)}) · Errors {formatted(port.errors)} · Discards {formatted(port.discards)}</p>
              {openPort === port.id && <div className="mt-3"><PortHistory key={`${port.id}-${range}`} device={device} port={port} range={range} /></div>}
            </div>)}
          </section>
          <section className="space-y-2"><h3 className="font-semibold">Hardware sensors</h3>
            {details.sensors.length ? <ul>{details.sensors.map((sensor, index) => <li key={`${sensor.name}-${index}`}>{sensor.name} · {sensor.value} {sensor.unit}</li>)}</ul> : <p className="ops-muted">No fresh hardware sensor readings.</p>}
          </section>
          <section className="space-y-2"><h3 className="font-semibold">Active Zabbix problems</h3>
            {details.problems.length ? <ul className="space-y-1">{details.problems.map(problem => <li key={problem.id} className="text-sm"><Badge status={problem.severity >= 4 ? 'down' : 'warning'}>Severity {problem.severity}</Badge> {problem.title}{problem.suppressed && ' · Suppressed'}</li>)}</ul>
              : <p className="ops-muted">{details.device.problems_known ? 'No active problems reported.' : 'Problem data is unavailable.'}</p>}
          </section>
        </>}
      </>}
    </div>
  </Modal>;
}

function Card({ device, weights, onOpen }: { device: Device; weights: Record<Resource, number>; onOpen: () => void }) {
  const usage = usageScore(device.resources, weights);
  const [tone, status] = statusFor(device);
  const [anchor, setAnchor] = useState<DOMRect>();
  const previewId = useId();
  const preview = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (!anchor || !preview.current) return;
    const { width, height } = preview.current.getBoundingClientRect();
    const preferredTop = anchor.bottom + 6 + height <= window.innerHeight - 8 ? anchor.bottom + 6 : anchor.top - height - 6;
    preview.current.style.left = `${Math.max(8, Math.min(anchor.left, window.innerWidth - width - 8))}px`;
    preview.current.style.top = `${Math.max(8, Math.min(preferredTop, window.innerHeight - height - 8))}px`;
  }, [anchor]);
  return <div className="infra-card-wrapper" onMouseLeave={() => setAnchor(undefined)}>
    <button className="infra-device-card" type="button" data-status={tone}
      aria-describedby={anchor ? previewId : undefined} onClick={() => { setAnchor(undefined); onOpen(); }}
      onMouseEnter={event => setAnchor(event.currentTarget.getBoundingClientRect())}
      onFocus={event => setAnchor(event.currentTarget.getBoundingClientRect())} onBlur={() => setAnchor(undefined)}
      onKeyDown={event => { if (event.key === 'Escape') setAnchor(undefined); }}>
      <span className="infra-device-name"><strong>{device.name}</strong><span className="font-mono ops-muted">{device.ip || 'No IP reported'}</span><span className="sr-only">{status}</span></span>
      <span className="infra-scores">
        <span data-level={usageTone(usage.score)}><small>Usage</small><strong>{pct(usage.score)}</strong></span>
        <span data-level={healthTone(device.health)}><small>Health</small><strong>{pct(device.health)}</strong></span>
        {usage.available.length > 0 && usage.available.length < 3 && <small>{usage.available.length}/3 measured</small>}
      </span>
    </button>
    {anchor && <div ref={preview} id={previewId} role="tooltip" className="infra-preview dash-panel">
      <Breakdown device={device} weights={weights} />
      {device.penalties[0] && <p className="infra-preview-reason">{device.penalties[0].reason}</p>}
    </div>}
  </div>;
}

function readOverrides(key: string): Record<string, Category> {
  try {
    const stored = JSON.parse(localStorage.getItem(key) ?? '{}');
    const entries = Object.entries(stored ?? {}).filter((entry): entry is [string, Category] => categories.includes(entry[1] as Category));
    return Object.fromEntries(entries);
  } catch { return {}; }
}

function totalTraffic(ports: Port[], direction: 'in' | 'out') {
  const values = ports.flatMap(port => port[direction] ?? []);
  return values.length ? values.reduce((sum, value) => sum + value, 0) : null;
}

export function InfrastructureMonitoring({ fleet, sites, refresh, onNavigate }: {
  fleet?: FleetOverview; sites?: Site[]; refresh: number; onNavigate?: () => void;
}) {
  const { user } = useAuth();
  const storageKey = `zaddesh-infrastructure-categories-${user?.username ?? ''}`;
  const [overrides, setOverrides] = useState(() => readOverrides(storageKey));
  const [summary, setSummary] = useState<Summary>();
  const [failed, setFailed] = useState(false);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('all');
  const [status, setStatus] = useState('all');
  const [site, setSite] = useState('all');
  const [sort, setSort] = useState('name');
  const [selected, setSelected] = useState<string>();
  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    async function poll() {
      let interval = 25;
      try {
        const result = await request<Summary>('/infrastructure-monitoring', { signal: controller.signal });
        if (!controller.signal.aborted) { setSummary(result); setFailed(false); interval = result.refresh_seconds; }
      } catch {
        if (!controller.signal.aborted) setFailed(true);
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(poll, interval * 1000);
      }
    }
    void poll();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [refresh]);

  const inventory = new Map(fleet?.devices.map(device => [device.id, device]));
  const remote = new Map(summary?.devices.map(device => [device.id, failed ? { ...device, resources: {}, health: null, penalties: [],
    factors: { interfaces: false, hardware: false, performance: false }, problems_known: false, severity: 0, status: 'unknown' } : device]));
  for (const item of inventory.values()) {
    if (remote.has(item.id)) continue;
    remote.set(item.id, { id: item.id, name: item.name, ip: item.ip ?? '', category: 'Other Devices', severity: 0,
      status: { up: 'online', down: 'offline', warning: 'warning', unmanaged: 'disabled', unknown: 'unknown' }[item.status],
      resources: {}, health: null, penalties: [], factors: { interfaces: false, hardware: false, performance: false },
      critical_count: 0, problems_known: false, problem_count: 0, last_seen: null });
  }
  const all = [...remote.values()].map(device => ({ ...device, category: overrides[device.id] ?? device.category }));
  const weightsFor = (device: Device) => summary?.config.weights[device.category] ?? defaults;
  const order = (device: Device) => sort === 'usage' ? -(usageScore(device.resources, weightsFor(device)).score ?? -1) : device.health ?? 101;
  const visible = all.filter(device => (category === 'all' || category === device.category)
    && (site === 'all' || site === String(inventory.get(device.id)?.site_id ?? 'unassigned'))
    && (status === 'all' || (status === 'offline' ? device.status === 'offline' : status === 'critical' ? device.severity >= 4 : statusFor(device)[0] === 'warning'))
    && (!query.trim() || `${device.name} ${device.ip}`.toLowerCase().includes(query.trim().toLowerCase())))
    .sort((a, b) => (sort !== 'name' ? order(a) - order(b) : 0) || a.name.localeCompare(b.name));
  const active = all.find(device => device.id === selected);
  function changeCategory(value: Category | '') {
    if (!active) return;
    const next = { ...overrides };
    if (value) next[active.id] = value;
    else delete next[active.id];
    setOverrides(next);
    try { localStorage.setItem(storageKey, JSON.stringify(next)); } catch { /* In-memory override is still active. */ }
  }

  const counts = [['Total devices', all.length], ['Online', all.filter(device => device.status === 'online').length],
    ['Offline', all.filter(device => device.status === 'offline').length], ['Critical', all.filter(device => device.severity >= 4).length],
    ['Warning', all.filter(device => statusFor(device)[0] === 'warning').length]];
  return <section className="dash-panel infra-monitoring self-start" aria-labelledby="devices-title">
    <header className="dash-panel-head"><div><h2 id="devices-title"><Server size={16} /> Infrastructure Device Monitoring</h2></div>
      {onNavigate && <button className="ops-button" onClick={onNavigate}>View devices <ArrowRight size={14} /></button>}
    </header>
    <div className="dash-panel-body space-y-3">
      <dl className="infra-summary">{counts.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>
      {failed && <p className="ops-notice" role="status">Zabbix monitoring data is temporarily unavailable.</p>}
      {!failed && summary?.at && <p className="ops-muted text-xs">Snapshot {new Date(summary.at).toLocaleTimeString()}</p>}
      <div className="flex flex-wrap gap-2">
        <input className="dash-select min-w-40" aria-label="Search infrastructure by name or IP" placeholder="Search name or IP" value={query} onChange={event => setQuery(event.target.value)} />
        <select className="dash-select" aria-label="Filter infrastructure by category" value={category} onChange={event => setCategory(event.target.value)}><option value="all">All categories</option>{categories.map(value => <option key={value}>{value}</option>)}</select>
        <select className="dash-select" aria-label="Filter infrastructure by status" value={status} onChange={event => setStatus(event.target.value)}><option value="all">Any status</option><option value="offline">Offline</option><option value="critical">Critical</option><option value="warning">Warning</option></select>
        <select className="dash-select" aria-label="Filter devices by site" value={site} onChange={event => setSite(event.target.value)}><option value="all">All sites</option>{sites?.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}<option value="unassigned">Unassigned</option></select>
        <select className="dash-select" aria-label="Sort infrastructure devices" value={sort} onChange={event => setSort(event.target.value)}><option value="name">Device name</option><option value="usage">Highest usage</option><option value="health">Lowest health</option></select>
      </div>
      {categories.filter(value => category === 'all' || category === value).map(value => {
        const group = visible.filter(device => device.category === value);
        return <details key={value} className="infra-category" open={group.length > 0}>
          <summary><ChevronDown size={14} aria-hidden="true" /> {value}<span className="ops-muted">{group.length}</span></summary>
          {group.length ? <div className="infra-cards">{group.map(device => <Card key={device.id} device={device} weights={weightsFor(device)} onOpen={() => setSelected(device.id)} />)}</div>
            : <p className="px-3 pb-3 text-xs ops-muted">No matching devices.</p>}
        </details>;
      })}
      {!visible.length && <p className="ops-muted text-sm" role="status">{!summary && !fleet && !failed ? 'Loading infrastructure devices…' : 'No devices match this view.'}</p>}
    </div>
    {active && <DevicePanel key={active.id} device={active} policy={summary?.config.network_policy} weights={weightsFor(active)} canReadDetails={user?.role === 'admin'}
      category={overrides[active.id] ?? ''} onCategory={changeCategory} onClose={() => setSelected(undefined)} />}
  </section>;
}
