import { useEffect, useState } from 'react';
import { Badge } from './Badge';
import { Card } from './Card';
import { EntryDetails, logTitle } from './EntryDetails';
import { request } from '../../services/api';
import type { Device } from '../../types';

const endpoint = '/zabbix';
type Connection = { configured: boolean; api_url: string | null; version: string | null };
type Host = { hostid: string; name: string; host: string; status: string; interfaces: { ip: string; dns: string; useip: string; available?: string }[] };
export type ZabbixInventoryHost = Host & { local_device_ids: number[] };
type Inventory = { hosts: Host[]; limited: boolean };
type Problem = { eventid: string; name: string; severity: string; clock: string; acknowledged: string };
type Reading = { up: boolean; timestamp: string };
type Validation = { status: 'match' | 'mismatch'; zabbix: Reading; zaddesh: Reading; skew_seconds: number }
  | { status: 'unavailable'; message: string };
const severities = ['Not classified', 'Info', 'Warning', 'Average', 'High', 'Critical'];

export function ZabbixSettings() {
  const [connection, setConnection] = useState<Connection>();
  const [url, setUrl] = useState('');
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    void request<Connection>(endpoint + '/connection', { signal: controller.signal }).then(value => {
      setConnection(value);
      setUrl(current => current || (value.api_url ?? ''));
    }).catch(failure => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, []);

  async function change(remove: boolean) {
    setBusy(true); setError(''); setMessage('');
    try {
      if (remove) {
        await request(endpoint + '/connection', { method: 'DELETE' });
        setConnection({ configured: false, api_url: null, version: null }); setUrl(''); setToken('');
        setMessage('Zabbix connection removed.');
      } else {
        const result = await request<Connection>(endpoint + '/connection', {
          method: 'PUT', body: JSON.stringify({ api_url: url.trim(), token: token.trim() }),
        });
        setConnection(result); setUrl(result.api_url ?? ''); setToken('');
        setMessage('Zabbix accepted the token and allowed host, item and problem reads.');
      }
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return <Card title="Zabbix connection" subtitle={connection?.configured ? `Connected · Zabbix ${connection.version}` : 'Read-only integration'}>
    <form className="flex flex-wrap items-end gap-3" onSubmit={event => { event.preventDefault(); void change(false); }}>
      <label className="min-w-0 flex-1 text-sm">Zabbix URL
        <input className="dash-select mt-1 block w-full" type="url" required maxLength={1900} value={url}
          placeholder="https://zabbix.example.com/zabbix" onChange={event => setUrl(event.target.value)} />
      </label>
      <label className="min-w-0 flex-1 text-sm">Zabbix API token
        <input className="dash-select mt-1 block w-full" type="password" autoComplete="off" required maxLength={512}
          value={token} onChange={event => setToken(event.target.value)} placeholder={connection?.configured ? 'Enter token to replace it' : undefined} />
      </label>
      <button className="ops-button" disabled={busy || !url.trim() || !token.trim()} type="submit">{busy ? 'Working…' : 'Test and save'}</button>
      {connection?.configured && <button className="ops-button" disabled={busy} type="button" onClick={() => void change(true)}>Disconnect</button>}
    </form>
    <p className="mt-3 text-sm ops-muted">Use a token with read access to the required Zabbix host groups and the host, item and problem API methods.</p>
    {url.trim().toLowerCase().startsWith('http://') && <p className="mt-2 text-sm ops-muted">HTTP sends the token unencrypted between ZadDesh and Zabbix.</p>}
    {message && <p role="status" className="mt-2 text-sm">{message}</p>}
    {error && <p role="alert" className="mt-2 ops-notice">{error}</p>}
  </Card>;
}

export function ZabbixDevices({ devices }: { devices: Device[] }) {
  const [connection, setConnection] = useState<Connection>();
  const [inventory, setInventory] = useState<Inventory>();
  const [problems, setProblems] = useState<Problem[]>();
  const [error, setError] = useState('');
  const [hostError, setHostError] = useState('');
  const [problemError, setProblemError] = useState('');
  const [draft, setDraft] = useState('');
  const [search, setSearch] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [host, setHost] = useState('');
  const [device, setDevice] = useState('');
  const [validation, setValidation] = useState<Validation>();
  const [validationError, setValidationError] = useState('');
  const [validating, setValidating] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const signal = controller.signal;
    async function load() {
      try {
        const status = await request<Connection>(endpoint + '/connection', { signal });
        if (signal.aborted) return;
        setConnection(status); setError('');
        if (!status.configured) return;
        const query = new URLSearchParams({ q: search });
        const [hostResult, problemResult] = await Promise.allSettled([
          request<Inventory>(`${endpoint}/hosts?${query}`, { signal }),
          request<Problem[]>(endpoint + '/problems', { signal }),
        ]);
        if (signal.aborted) return;
        if (hostResult.status === 'fulfilled') setInventory(hostResult.value);
        if (problemResult.status === 'fulfilled') setProblems(problemResult.value);
        setHostError(hostResult.status === 'rejected' ? hostResult.reason.message : '');
        setProblemError(problemResult.status === 'rejected' ? problemResult.reason.message : '');
      } catch (failure) {
        if (!signal.aborted) setError((failure as Error).message);
      }
    }
    void load();
    const timer = window.setInterval(load, 60_000);
    return () => { window.clearInterval(timer); controller.abort(); };
  }, [refresh, search]);

  async function validate() {
    setValidating(true); setValidation(undefined); setValidationError('');
    try {
      setValidation(await request<Validation>(`${endpoint}/validation?${new URLSearchParams({ device_id: device, host_id: host })}`));
    } catch (failure) {
      setValidationError((failure as Error).message);
    } finally {
      setValidating(false);
    }
  }

  if (error) return <p role="alert" className="ops-notice">{error}</p>;
  if (!connection) return <p role="status" className="ops-empty">Loading Zabbix connection…</p>;
  if (!connection.configured) return <p className="ops-empty">Connect Zabbix in Settings to view its hosts and problems.</p>;

  return <div className="space-y-4">
    <Card title="Zabbix hosts" subtitle="Read-only inventory from Zabbix">
      <form className="mb-3 flex flex-wrap items-end gap-2" onSubmit={event => {
        event.preventDefault(); setSearch(draft.trim()); setRefresh(value => value + 1);
      }}>
        <label className="text-sm">Host name<input className="dash-select block" value={draft} maxLength={120} onChange={event => setDraft(event.target.value)} /></label>
        <button className="ops-button" type="submit">Search</button>
        <button className="ops-button" type="button" onClick={() => setRefresh(value => value + 1)}>Refresh Zabbix</button>
      </form>
      {hostError && <p role="alert" className="ops-notice">{hostError}</p>}
      {!inventory && !hostError && <p role="status">Loading hosts…</p>}
      {inventory?.limited && <p className="ops-muted text-sm">Showing the first 100 matches. Search by host name for a narrower list.</p>}
      {inventory?.hosts.length === 0 && <p className="ops-empty">No accessible Zabbix hosts match.</p>}
      <ul className="divide-y divide-slate-500/25">{inventory?.hosts.map(item => <li key={item.hostid} className="flex flex-wrap items-center gap-3 py-2 text-sm">
        <strong>{item.name || item.host}</strong>
        <Badge status={item.status === '0' ? 'info' : 'unknown'}>{item.status === '0' ? 'Monitored' : 'Disabled'}</Badge>
        <span className="ops-muted break-all">{item.interfaces.map(value => value.useip === '1' ? value.ip : value.dns).filter(Boolean).join(', ')}</span>
      </li>)}</ul>
    </Card>
    <Card title="Zabbix problems" subtitle="Latest 100 unresolved problems visible to the connected account">
      {problemError && <p role="alert" className="ops-notice">{problemError}</p>}
      {!problems && !problemError && <p role="status">Loading problems…</p>}
      {problems?.length === 0 && <p className="ops-empty">No unresolved problems reported by Zabbix.</p>}
      <ul className="divide-y divide-slate-500/25">{problems?.map(item => <li className="py-2 text-sm" key={item.eventid}>
        <EntryDetails title={logTitle(item.name)}><p>{item.name}</p><p>{item.acknowledged === '1' ? 'Acknowledged' : 'Unacknowledged'}</p></EntryDetails>
        <div className="flex flex-wrap items-center gap-2"><Badge status={severities[Number(item.severity)] || 'unknown'} />
          <time>{new Date(Number(item.clock) * 1000).toLocaleString()}</time></div>
      </li>)}</ul>
    </Card>
    <Card title="Validate ICMP reachability">
      <form className="flex flex-wrap items-end gap-3" onSubmit={event => { event.preventDefault(); void validate(); }}>
        <label className="text-sm">ZadDesh device<select className="dash-select block" value={device} disabled={validating} onChange={event => {
          setDevice(event.target.value); setValidation(undefined); setValidationError('');
        }}><option value="">Select a device</option>{devices.map(item => <option key={item.id} value={item.id}>{item.name} · {item.ip}</option>)}</select></label>
        <label className="text-sm">Zabbix host<select className="dash-select block" value={host} disabled={validating} onChange={event => {
          setHost(event.target.value); setValidation(undefined); setValidationError('');
        }}><option value="">Select a host</option>{inventory?.hosts.map(item => <option key={item.hostid} value={item.hostid}>{item.name || item.host}</option>)}</select></label>
        <button className="ops-button" disabled={validating || !host || !device} type="submit">{validating ? 'Comparing…' : 'Compare ICMP'}</button>
      </form>
      {validationError && <p role="alert" className="mt-3 ops-notice">{validationError}</p>}
      {validation && <div role="status" className="mt-3 space-y-2 text-sm">{validation.status === 'unavailable' ? <p>{validation.message}</p> : <>
        <Badge status={validation.status === 'match' ? 'ok' : 'warning'}>{validation.status === 'match' ? 'Match' : 'Mismatch'}</Badge>
        <p>ZadDesh {validation.zaddesh.up ? 'up' : 'down'} · {new Date(validation.zaddesh.timestamp).toLocaleString()}</p>
        <p>Zabbix {validation.zabbix.up ? 'up' : 'down'} · {new Date(validation.zabbix.timestamp).toLocaleString()}</p>
        <p className="ops-muted">Samples were {validation.skew_seconds} seconds apart.</p>
      </>}</div>}
    </Card>
  </div>;
}
