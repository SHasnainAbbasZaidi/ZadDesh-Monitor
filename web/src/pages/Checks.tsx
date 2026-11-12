import React, { useEffect, useState } from 'react';
import { Card } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Modal } from '../components/ui/Modal';
import { EntryDetails, logTitle } from '../components/ui/EntryDetails';
import { SnmpCheckForm } from '../components/ui/SnmpCheckForm';
import { useAuth } from '../context/AuthContext';
import { api } from '../services/api';
import { Check } from '../types';
import { RefreshCw, Clock } from 'lucide-react';

export const Checks: React.FC = () => {
  const [checks, setChecks] = useState<Check[]>([]);
  const [loading, setLoading] = useState(true);
  const [typeFilter, setTypeFilter] = useState<string>('all');
  const [refresh, setRefresh] = useState(0);
  const [showSnmpForm, setShowSnmpForm] = useState(false);
  const [error, setError] = useState('');
  const { user } = useAuth();

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    api.getChecks(typeFilter === 'all' ? undefined : { check_type: typeFilter })
      .then(result => { if (active) setChecks(result); })
      .catch(() => { if (active) setError('Could not load checks. Try refreshing.'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [typeFilter, refresh]);

  const filteredChecks = checks.filter(
    (c) => typeFilter === 'all' || c.check_type === typeFilter
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">
            Monitoring Checks
          </h1>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
            Active synthetic probes and metric collection schedules
          </p>
        </div>
        <div className="flex gap-2">
          {user?.role === 'admin' && <button className="ops-button" onClick={() => setShowSnmpForm(true)}>Add SNMP Check</button>}
          <button className="ops-button" aria-label="Refresh checks" onClick={() => setRefresh(value => value + 1)}>
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* Filter Tabs */}
      <div className="flex flex-wrap items-center gap-2">
        {['all', 'icmp', 'http', 'https', 'tcp', 'snmp'].map((t) => (
          <button
            key={t}
            aria-pressed={typeFilter === t}
            onClick={() => setTypeFilter(t)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium uppercase tracking-wider transition-colors ${
              typeFilter === t
                ? 'bg-sky-500 text-white'
                : 'bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800'
            }`}
          >
            {t.toUpperCase()}
          </button>
        ))}
      </div>

      {error && <p role="alert" className="ops-empty">{error}</p>}
      {!loading && !error && filteredChecks.length === 0 && (
        <p className="ops-empty">{typeFilter === 'snmp' ? 'No SNMP checks configured yet.' : 'No checks match this filter.'}</p>
      )}
      {showSnmpForm && <Modal title="Add SNMP Check" isOpen onClose={() => setShowSnmpForm(false)}>
        <SnmpCheckForm onCreated={check => {
          setChecks(current => [...current, check]);
          setShowSnmpForm(false);
          setTypeFilter('snmp');
          setRefresh(value => value + 1);
        }} />
      </Modal>}

      <Card>
        <div className="overflow-x-auto -mx-5 -my-5">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-800 text-[11px] font-mono uppercase text-slate-400">
              <tr>
                <th className="px-5 py-3.5">Status</th>
                <th className="px-5 py-3.5">Check Name</th>
                <th className="px-5 py-3.5">Type</th>
                <th className="px-5 py-3.5">Target</th>
                <th className="px-5 py-3.5">Interval</th>
                <th className="px-5 py-3.5">Diagnostics / Last Result</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-800/80">
              {filteredChecks.map((chk) => (
                <tr
                  key={chk.id}
                  className="hover:bg-slate-50/50 dark:hover:bg-slate-800/40 transition-colors"
                >
                  <td className="px-5 py-3.5 whitespace-nowrap">
                    <Badge status={chk.state?.current_status || 'unknown'} />
                  </td>
                  <td className="px-5 py-3.5 font-medium text-slate-900 dark:text-slate-100 whitespace-nowrap">
                    {chk.name}
                  </td>
                  <td className="px-5 py-3.5 font-mono text-xs uppercase text-slate-500 dark:text-slate-400 whitespace-nowrap">
                    <span className="px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-800">
                      {chk.check_type}
                    </span>
                  </td>
                  <td className="px-5 py-3.5 font-mono text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap">
                    {chk.target}
                  </td>
                  <td className="px-5 py-3.5 text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap">
                    <span className="flex items-center space-x-1">
                      <Clock className="w-3 h-3 text-slate-400" />
                      <span>{chk.interval_seconds}s</span>
                    </span>
                  </td>
                  <td className="px-5 py-3.5 text-xs text-slate-600 dark:text-slate-300">
                    {chk.state?.message ? <EntryDetails title={logTitle(chk.state.message)}>
                      <p>{chk.state.message}</p>
                      <p>{chk.check_type.toUpperCase()} · {chk.target}</p>
                    </EntryDetails> : 'Awaiting polling run'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
};

