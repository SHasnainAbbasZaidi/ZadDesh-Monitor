import { useEffect, useState } from 'react';
import { Card } from '../components/ui/Card';
import { UserManagement } from '../components/ui/UserManagement';
import { ZabbixSettings } from '../components/ui/Zabbix';
import { api } from '../services/api';
import type { SystemInfo } from '../types';

export function Settings() {
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    api.getSystemInfo().then(value => { if (active) setInfo(value); }).catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, []);
  return <div className="space-y-6">
    <header>
      <p className="ops-eyebrow">Administration</p>
      <h1 className="ops-heading">System settings</h1>
      <p className="ops-muted mt-2">Manage accounts and review the core runtime.</p>
    </header>
    <UserManagement />
    <ZabbixSettings />
    {error && <p role="alert" className="ops-notice">Could not read runtime information from the core.</p>}
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
      <Card title="Runtime" subtitle={info ? 'Reported by the core' : error ? 'Unavailable' : 'Loading'}>
        <dl className="divide-y divide-slate-200 dark:divide-slate-700">
          {[
            ['Version', info?.version],
            ['Database engine', info?.database_type],
            ['Data mode', info?.environment],
            ['Core uptime', info ? `${Math.floor(info.uptime_seconds).toLocaleString()} s` : undefined],
            ['Registered collectors', info?.counts.collectors],
          ].map(([label, value]) => <div key={label} className="flex justify-between gap-3 py-3 text-sm">
            <dt className="ops-muted">{label}</dt><dd className="font-mono">{value ?? '—'}</dd>
          </div>)}
        </dl>
      </Card>
      <Card title="Collector enrollment" subtitle="Configured on the server">
        <p className="ops-muted text-sm leading-relaxed">Enroll a collector with a temporary enrollment credential and a unique collector token. Tokens are managed on the host and are never displayed in the dashboard.</p>
        <p className="ops-muted text-sm leading-relaxed mt-3">See the deployment instructions for enrolling or replacing a collector.</p>
      </Card>
    </div>
    <Card title="Metric storage">
      <p className="ops-muted text-sm leading-relaxed">The core currently queries raw samples. Automatic rollups and retention are unavailable, so database size will grow as samples arrive.</p>
    </Card>
  </div>;
}

