import { useEffect, useState } from 'react';
import { request } from '../../services/api';
import { EntryDetails } from './EntryDetails';
import { formatMetricValue } from './formatMetricValue';
import windowsServices from '../../agents/enable-services-windows.ps1?raw';
import linuxServices from '../../agents/enable-services-linux.sh?raw';
import { includeEventLogInstaller } from '../../agents/includeEventLogInstaller';

type Snapshot = { status: 'never' | 'queued' | 'running' | 'complete' | 'failed'; collected_at: string | null; message: string | null;
  processes_available: boolean; services_available: boolean; client_logs_available: boolean; client_logs_error: string | null;
  processes: { pid: number; name: string; path: string; status: string; kind: string }[];
  services: { name: string; status: string }[];
  health: { agent_name?: string; cpu_load_percent?: number | null; storage?: { name: string; used_bytes: number; total_bytes: number; utilization: number }[] } };

function isProcess(item: Snapshot['services'][number]): item is Snapshot['processes'][number] {
  return 'pid' in item && typeof item.pid === 'number';
}

function downloadServices(platform: 'windows' | 'linux') {
  const url = URL.createObjectURL(new Blob([includeEventLogInstaller(platform === 'windows' ? windowsServices : linuxServices, platform)], { type: 'text/plain' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = platform === 'windows' ? 'enable-services-windows.ps1' : 'enable-services-linux.sh';
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function WorkstationInventory({ deviceId }: { deviceId: number }) {
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [tab, setTab] = useState<'processes' | 'services'>('processes');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    const base = `/devices/${deviceId}/inventory`;
    async function poll() {
      try {
        const value = await request<Snapshot>(base, { signal: controller.signal });
        if (!controller.signal.aborted) {
          setSnapshot(value); setError('');
          if (value.status === 'complete' || value.status === 'failed') window.dispatchEvent(new CustomEvent('zaddesh-device-logs', { detail: deviceId }));
          if (value.status === 'queued' || value.status === 'running') timer = window.setTimeout(poll, 2000);
        }
      } catch (failure) { if (!controller.signal.aborted) setError((failure as Error).message); }
    }
    void request<Snapshot>(base + '/refresh', { method: 'POST', signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) { setSnapshot(value); void poll(); }
    }).catch(failure => { if (!controller.signal.aborted) setError(failure.message); });
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [deviceId, refresh]);
  const pending = snapshot?.status === 'queued' || snapshot?.status === 'running';
  const rows = (tab === 'processes' ? snapshot?.processes : snapshot?.services)?.filter(item => Object.values(item).some(value => String(value).toLowerCase().includes(query.toLowerCase()))) ?? [];
  const available = tab === 'processes' ? snapshot?.processes_available : snapshot?.services_available;
  const shown = rows.slice(page * 25, page * 25 + 25);
  return <section className="space-y-3" aria-labelledby="workstation-inventory-title">
    <header className="flex flex-wrap items-center justify-between gap-2"><div>
      <h3 id="workstation-inventory-title" className="font-semibold">Processes &amp; services</h3>
      <p className="ops-muted text-xs">{snapshot?.collected_at ? `SNMP snapshot · ${new Date(snapshot.collected_at + (/Z$|[+-]\d\d:\d\d$/.test(snapshot.collected_at) ? '' : 'Z')).toLocaleString()}` : 'Collecting an on-demand SNMP snapshot'}</p>
    </div><button type="button" className="ops-button" disabled={pending} onClick={() => { setError(''); setRefresh(value => value + 1); }}>{pending ? snapshot?.status === 'queued' ? 'Waiting for collector…' : 'Reading SNMP…' : 'Refresh snapshot'}</button></header>
    {error && <p role="alert" className="ops-notice">{error}</p>}
    {snapshot?.status === 'failed' && <EntryDetails title="SNMP snapshot failed"><p>{snapshot.message}</p></EntryDetails>}
    {snapshot?.client_logs_error && snapshot.client_logs_available && <p role="status" className="ops-muted text-xs">{snapshot.client_logs_error}</p>}
    {snapshot?.status === 'complete' && !snapshot.client_logs_available && <p className="ops-muted text-xs">{snapshot.client_logs_error || 'Client issue-log extension unavailable. Use the client update under Services to enable it.'}</p>}
    {snapshot?.health && <dl className="grid gap-2 text-xs sm:grid-cols-2">
      <div><dt className="ops-muted">SNMP name</dt><dd>{snapshot.health.agent_name || 'Unavailable'}</dd></div>
      <div><dt className="ops-muted">CPU load</dt><dd>{formatMetricValue(snapshot.health.cpu_load_percent, '%')}</dd></div>
      {snapshot.health.storage?.map(item => <div key={item.name}><dt className="ops-muted">{item.name}</dt><dd>{formatMetricValue(item.used_bytes, 'B')} / {formatMetricValue(item.total_bytes, 'B')} · {item.utilization}%</dd></div>)}
    </dl>}
    <div className="settings-sections flex flex-wrap items-center gap-2">
      <button className="ops-button" type="button" aria-pressed={tab === 'processes'} onClick={() => { setTab('processes'); setPage(0); }}>Processes ({snapshot?.processes.length ?? '—'})</button>
      <button className="ops-button" type="button" aria-pressed={tab === 'services'} onClick={() => { setTab('services'); setPage(0); }}>Services ({snapshot?.services.length ?? '—'})</button>
      <input type="search" className="dash-select" aria-label="Search processes and services" placeholder="Search snapshot" value={query} onChange={event => { setQuery(event.target.value); setPage(0); }} />
    </div>
    {snapshot?.status === 'complete' && !available && <p role="status" className="ops-empty">{tab === 'processes' ? 'This SNMP agent did not expose the running-software table.' : 'This SNMP agent did not expose the ZadDesh service inventory extension. Enable the extension on the client to collect services.'}</p>}
    {tab === 'services' && <details className="text-xs"><summary className="cursor-pointer">Enable services and issue logs on an existing client</summary>
      <p className="ops-muted my-2">Run the update script from an Administrator terminal or with sudo, then refresh this snapshot.</p>
      <div className="flex flex-wrap gap-2"><button type="button" className="ops-button" onClick={() => downloadServices('windows')}>Windows client update</button>
        <button type="button" className="ops-button" onClick={() => downloadServices('linux')}>Linux client update</button></div>
    </details>}
    {available && rows.length === 0 && <p className="ops-empty">No collected entries match this search.</p>}
    {!!shown.length && <div className="overflow-x-auto"><table className="w-full text-left text-xs"><caption className="sr-only">Collected {tab}</caption>
      <thead><tr>{(tab === 'processes' ? ['PID', 'Name', 'Type', 'State', 'Executable'] : ['Service', 'State']).map(label => <th key={label} scope="col" className="border-b border-slate-500/25 p-2">{label}</th>)}</tr></thead>
      <tbody>{shown.map(item => isProcess(item) ? <tr key={item.pid}>
        <td className="p-2 font-mono">{item.pid}</td><td className="p-2">{item.name}</td><td className="p-2">{item.kind}</td><td className="p-2">{item.status}</td><td className="break-all p-2">{item.path || 'Unavailable'}</td>
      </tr> : <tr key={item.name}><td className="p-2">{item.name}</td><td className="p-2">{item.status}</td></tr>)}</tbody>
    </table></div>}
    {rows.length > 25 && <div className="flex items-center gap-2 text-xs"><button className="ops-button" disabled={!page} onClick={() => setPage(value => value - 1)}>Previous</button>
      <span>{page * 25 + 1}–{Math.min((page + 1) * 25, rows.length)} of {rows.length}</span><button className="ops-button" disabled={(page + 1) * 25 >= rows.length} onClick={() => setPage(value => value + 1)}>Next</button></div>}
  </section>;
}
