import { useEffect, useState } from 'react';
import { Activity } from 'lucide-react';
import { Badge } from './Badge';
import { MetricChart } from './MetricChart';
import { api } from '../../services/api';
import { useTheme } from '../../context/ThemeContext';

const protocols = { icmp: 'ICMP', http: 'HTTP(S)', tcp: 'TCP' };
const palette = {
  dark: ['#38bdf8', '#c084fc', '#fbbf24', '#34d399', '#fb7185', '#a5b4fc'],
  light: ['#0369a1', '#7e22ce', '#a16207', '#047857', '#be123c', '#4338ca'],
};
type Latency = Awaited<ReturnType<typeof api.getProbeLatency>>;

export function OverviewLatency({ minutes, updatedAt }: { minutes: number; updatedAt?: number }) {
  const { theme } = useTheme();
  const [selection, setSelection] = useState('all');
  const [protocol, setProtocol] = useState<keyof typeof protocols>('icmp');
  const [result, setResult] = useState<{ key: string; data: Latency }>();
  const [error, setError] = useState('');
  const key = [selection, protocol, minutes].join('/');
  const current = result?.key === key ? result.data : undefined;
  const devices = result?.data.devices ?? [];
  const displayed = selection === 'all' ? devices : devices.filter(device => String(device.id) === selection);
  const nameCounts = new Map<string, number>();
  for (const device of devices) nameCounts.set(device.name, (nameCounts.get(device.name) ?? 0) + 1);
  const deviceLabel = (device: Latency['devices'][number]) => (nameCounts.get(device.name) ?? 0) > 1
    ? `${device.name} (${device.ip})` : device.name;

  useEffect(() => {
    if (updatedAt === undefined) return;
    const controller = new AbortController();
    api.getProbeLatency(protocol, minutes, selection, controller.signal).then(data => {
      if (controller.signal.aborted) return;
      setError('');
      if (selection !== 'all' && !data.devices.some(device => String(device.id) === selection)) setSelection('all');
      setResult({ key, data });
    }).catch(() => {
      if (!controller.signal.aborted) setError('Could not load probe telemetry. Use Refresh to retry.');
    });
    return () => controller.abort();
  }, [key, minutes, protocol, selection, updatedAt]);

  const devicePositions = new Map(devices.map((device, index) => [device.id, { device, index }]));
  const channels = (current?.channels ?? []).flatMap(channel => {
    const position = devicePositions.get(channel.device_id);
    if (!position) return [];
    return [{
      id: channel.device_id,
      name: deviceLabel(position.device),
      color: palette[theme][position.index] ?? `hsl(${(position.index * 137.508) % 360}, 65%, ${theme === 'dark' ? 65 : 34}%)`,
      data: [...channel.latency, ...channel.failures.map(timestamp => ({ timestamp, value: null }))],
      failures: channel.failures.map(timestamp => ({ timestamp, value: 0 })),
      intervalSeconds: channel.interval_seconds,
    }];
  });
  const reporting = channels.filter(channel => channel.data.length).length;
  const hasOutages = channels.some(channel => channel.failures.length);
  const failedProbes = new Set(current?.channels.filter(channel => channel.probe_down).map(channel => channel.device_id));
  const down = displayed.filter(device => device.status === 'down' || failedProbes.has(device.id));

  return (
    <section className="dash-panel dash-latency" aria-labelledby="latency-heading">
      <header className="dash-panel-head">
        <div><h2 id="latency-heading"><Activity size={16} /> Probe latency</h2></div>
        <div className="dash-latency-selects">
          <select aria-label="Latency device" className="dash-select" value={selection} onChange={event => setSelection(event.target.value)}>
            <option value="all">All Devices</option>
            {devices.map(device => <option key={device.id} value={device.id}>{deviceLabel(device)}</option>)}
          </select>
          <select aria-label="Latency protocol" className="dash-select" value={protocol} onChange={event => setProtocol(event.target.value as keyof typeof protocols)}>
            {Object.entries(protocols).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </div>
      </header>
      <div className="dash-panel-body">
        {error && <p role="alert" className="mb-2 text-sm ops-muted">{error}</p>}
        {reporting > 0 && current ? <MetricChart title={protocols[protocol] + ' latency'} unit="ms" channels={channels}
          range={{ start: Date.parse(current.start), end: Date.parse(current.end) }} height={260} />
          : <div className="dash-chart-empty" role="status">
            <Activity size={25} />
            <strong>{error ? 'Telemetry unavailable' : !current ? 'Loading telemetry' : !devices.length ? 'No devices configured' : 'No samples in this range'}</strong>
            {current && !!devices.length && <span>Choose another protocol or time range, or wait for the next poll.</span>}
          </div>}
        {current && !!displayed.length && <footer className="dash-latency-footer">
          <span>{displayed.length} {displayed.length === 1 ? 'device' : 'devices'} · {reporting} reporting</span>
          {reporting > 0 && <span>Peak per {current.bucket_seconds}s</span>}
          {selection !== 'all' && <Badge status={displayed[0].status} />}
          {hasOutages && <span>× Failed probes</span>}
          {down.length > 0 && <ul aria-label="Devices with outages" className="dash-latency-outages">
            {down.map(device => <li key={device.id}><Badge status="down">{device.status === 'down' ? 'Down' : 'Probe failed'}</Badge> {deviceLabel(device)}</li>)}
          </ul>}
        </footer>}
      </div>
    </section>
  );
}
