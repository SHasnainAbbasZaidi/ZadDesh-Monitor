import { useEffect, useState } from 'react';
import { EntryDetails, logTitle } from '../components/ui/EntryDetails';
import { api, request, logSeverities, type InfrastructureLog } from '../services/api';

type SourceStatus = { zabbix?: { configured: boolean; status: string; last_poll: string | null; error: string | null }; error?: string };

export function Logs({ deviceId, embedded = false }: { deviceId?: number; embedded?: boolean }) {
  const [source, setSource] = useState('');
  const [severity, setSeverity] = useState('');
  const [origin, setOrigin] = useState<'zaddesh' | 'integration'>('zaddesh');
  const [provider, setProvider] = useState('');
  const [filters, setFilters] = useState({ source: '', severity: '', provider: '' });
  const [entries, setEntries] = useState<InfrastructureLog[]>();
  const [error, setError] = useState('');
  const [sourceStatus, setSourceStatus] = useState<SourceStatus>();

  useEffect(() => {
    const controller = new AbortController();
    let loading = false;
    const refresh = async () => {
      if (loading) return;
      loading = true;
      try {
        const [result, status] = await Promise.all([
          api.getLogs({ ...filters, provider: origin === 'integration' ? filters.provider : undefined, origin, device_id: deviceId }, controller.signal),
          origin === 'integration' && !embedded ? request<SourceStatus>('/logs/status', { signal: controller.signal }).catch(() => ({ error: 'Could not read integrated log collection status.' })) : Promise.resolve(undefined),
        ]);
        if (!controller.signal.aborted) {
          setEntries(result);
          setError('');
          setSourceStatus(status);
        }
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Could not load logs.');
      } finally {
        loading = false;
      }
    };
    const onSnapshot = (event: Event) => { if ((event as CustomEvent<number>).detail === deviceId) void refresh(); };
    window.addEventListener('zaddesh-device-logs', onSnapshot);
    void refresh();
    const timer = window.setInterval(refresh, 10_000);
    return () => {
      window.removeEventListener('zaddesh-device-logs', onSnapshot);
      controller.abort();
      window.clearInterval(timer);
    };
  }, [filters, origin, deviceId, embedded]);

  const titleId = embedded ? 'device-logs-title' : 'logs-title';
  const Heading = embedded ? 'h3' : 'h1';
  return <section className="space-y-4" aria-labelledby={titleId}>
    <Heading id={titleId} className={embedded ? 'font-semibold' : 'text-2xl font-semibold'}>{embedded ? 'Device logs' : 'Infrastructure Logs'}</Heading>
    <nav className="settings-sections flex flex-wrap gap-2" aria-label="Log sections">
      <button type="button" className="ops-button" aria-pressed={origin === 'zaddesh'} onClick={() => { setOrigin('zaddesh'); setEntries(undefined); }}>ZadDesh logs</button>
      <button type="button" className="ops-button" aria-pressed={origin === 'integration'} onClick={() => { setOrigin('integration'); setEntries(undefined); }}>Integrated app logs</button>
    </nav>
    {origin === 'integration' && sourceStatus?.error && <p role="alert" className="ops-notice">{sourceStatus.error}</p>}
    {origin === 'integration' && sourceStatus?.zabbix?.configured && <p className="ops-muted text-xs" role={sourceStatus.zabbix.error ? 'alert' : 'status'}>
      {sourceStatus.zabbix.error || (sourceStatus.zabbix.status === 'waiting' ? 'Waiting for the first Zabbix log collection.' : 'Zabbix event logs are archived automatically.')}
    </p>}
    <form className="flex flex-wrap items-end gap-3" onSubmit={event => {
      event.preventDefault();
      setEntries(undefined);
      setError('');
      setFilters({ source: source.trim(), severity, provider: origin === 'integration' ? provider : '' });
    }}>
      <label className="text-sm">Source
        <input className="dash-select mt-1 block" value={source} maxLength={255}
          onChange={event => setSource(event.target.value)} placeholder="All sources" />
      </label>
      {origin === 'integration' && <label className="text-sm">Application
        <select className="dash-select mt-1 block" value={provider} onChange={event => setProvider(event.target.value)}>
          <option value="">All applications</option>
          {Object.entries({ zabbix: 'Zabbix', grafana: 'Grafana', prometheus: 'Prometheus', solarwinds: 'SolarWinds', graylog: 'Graylog', loggly: 'Loggly', logzio: 'Logz.io', opentelemetry: 'OpenTelemetry', external: 'Other sources' }).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>}
      <label className="text-sm">Severity
        <select className="dash-select mt-1 block" value={severity} onChange={event => setSeverity(event.target.value)}>
          <option value="">All severities</option>
          {logSeverities.map(level => <option key={level} value={level}>{level}</option>)}
        </select>
      </label>
      <button type="submit" className="ops-button">Apply filters</button>
    </form>
    {error && <p role="alert" className="ops-empty">{error}</p>}
    {!entries && !error && <p role="status" className="ops-empty">Loading logs…</p>}
    {entries?.length === 0 && !error && <p className="ops-empty">No log entries match these filters.</p>}
    {!!entries?.length && <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">Latest infrastructure log entries</caption>
        <thead><tr className="border-b border-slate-500/25">
          {['Time', 'Source', 'Application', 'Severity', 'Title'].map(label => <th key={label} scope="col" className="p-3">{label}</th>)}
        </tr></thead>
        <tbody>{entries.map(entry => <tr key={entry.event_id} className="border-b border-slate-500/25 align-top">
          <td className="whitespace-nowrap p-3"><time dateTime={entry.timestamp}>{new Date(entry.timestamp).toLocaleString()}</time></td>
          <td className="break-all p-3">{entry.source}</td>
          <td className="p-3">{entry.provider ?? 'external'}</td>
          <td className="p-3 font-medium">{entry.severity}</td>
          <td className="p-3"><EntryDetails title={logTitle(entry.message)}><div className="font-mono text-xs">{entry.message}</div></EntryDetails></td>
        </tr>)}</tbody>
      </table>
    </div>}
  </section>;
}
