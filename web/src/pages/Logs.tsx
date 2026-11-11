import { useEffect, useState } from 'react';
import { EntryDetails, logTitle } from '../components/ui/EntryDetails';
import { api, logSeverities, type InfrastructureLog } from '../services/api';

export function Logs() {
  const [source, setSource] = useState('');
  const [severity, setSeverity] = useState('');
  const [filters, setFilters] = useState({ source: '', severity: '' });
  const [entries, setEntries] = useState<InfrastructureLog[]>();
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    let loading = false;
    const refresh = async () => {
      if (loading) return;
      loading = true;
      try {
        const result = await api.getLogs(filters, controller.signal);
        if (!controller.signal.aborted) {
          setEntries(result);
          setError('');
        }
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Could not load logs.');
      } finally {
        loading = false;
      }
    };
    void refresh();
    const timer = window.setInterval(refresh, 10_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [filters]);

  return <section className="space-y-4" aria-labelledby="logs-title">
    <h1 id="logs-title" className="text-2xl font-semibold">Infrastructure Logs</h1>
    <form className="flex flex-wrap items-end gap-3" onSubmit={event => {
      event.preventDefault();
      setEntries(undefined);
      setError('');
      setFilters({ source: source.trim(), severity });
    }}>
      <label className="text-sm">Source
        <input className="dash-select mt-1 block" value={source} maxLength={255}
          onChange={event => setSource(event.target.value)} placeholder="All sources" />
      </label>
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
          {['Time', 'Source', 'Severity', 'Title'].map(label => <th key={label} scope="col" className="p-3">{label}</th>)}
        </tr></thead>
        <tbody>{entries.map(entry => <tr key={entry.event_id} className="border-b border-slate-500/25 align-top">
          <td className="whitespace-nowrap p-3"><time dateTime={entry.timestamp}>{new Date(entry.timestamp).toLocaleString()}</time></td>
          <td className="break-all p-3">{entry.source}</td>
          <td className="p-3 font-medium">{entry.severity}</td>
          <td className="p-3"><EntryDetails title={logTitle(entry.message)}><div className="font-mono text-xs">{entry.message}</div></EntryDetails></td>
        </tr>)}</tbody>
      </table>
    </div>}
  </section>;
}
