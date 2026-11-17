import { lazy, Suspense, useEffect, useState } from 'react';
import { Mail, Plug, Settings as SettingsIcon } from 'lucide-react';
import { Card } from '../components/ui/Card';
import { UserManagement } from '../components/ui/UserManagement';
import { api } from '../services/api';
import type { SystemInfo } from '../types';

const EmailAlerts = lazy(() => import('./EmailAlerts'));
const ApiIntegrations = lazy(() => import('../components/ui/ApiIntegrations'));

export function Settings({ emailSelected, onSelectEmail }: { emailSelected: boolean; onSelectEmail: (selected: boolean) => void }) {
  const [integrationsSelected, setIntegrationsSelected] = useState(false);
  return <div className="space-y-6">
    <header>
      <p className="ops-eyebrow">Administration</p>
      <h1 className="ops-heading">System settings</h1>
      {!emailSelected && <p className="ops-muted mt-2">Manage accounts, API connections and the core runtime.</p>}
    </header>
    <nav aria-label="Settings sections" className="settings-sections flex flex-wrap gap-2">
      <button className="ops-button" type="button" aria-pressed={!emailSelected && !integrationsSelected} onClick={() => { setIntegrationsSelected(false); onSelectEmail(false); }}>
        <SettingsIcon size={16} aria-hidden="true" /> General
      </button>
      <button className="ops-button" type="button" aria-pressed={!emailSelected && integrationsSelected} onClick={() => { setIntegrationsSelected(true); onSelectEmail(false); }}>
        <Plug size={16} aria-hidden="true" /> API Integrations
      </button>
      <button className="ops-button" type="button" aria-pressed={emailSelected} onClick={() => onSelectEmail(true)}>
        <Mail size={16} aria-hidden="true" /> Email Alerts &amp; Notifications
      </button>
    </nav>
    {emailSelected ? <Suspense fallback={<p role="status">Loading email notifications…</p>}><EmailAlerts /></Suspense>
      : integrationsSelected ? <Suspense fallback={<p role="status">Loading API connections…</p>}><ApiIntegrations /></Suspense> : <GeneralSettings />}
  </div>;
}

function GeneralSettings() {
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    api.getSystemInfo().then(value => { if (active) setInfo(value); }).catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, []);
  return <div className="space-y-6">
    <UserManagement />
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

