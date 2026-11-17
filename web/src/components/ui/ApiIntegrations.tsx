import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { Plug, Trash2 } from 'lucide-react';
import { request } from '../../services/api';
import { ZabbixSettings } from './Zabbix';
import './apiIntegrations.css';

type Auth = 'none' | 'bearer' | 'basic' | 'api_key' | 'graylog_token';
type Connection = { provider: string; api_url: string; auth_type: Auth; username: string; header_name: string; has_secret: boolean };
type Provider = { provider: string; name: string; path: string; auth: Auth[]; default_auth: Auth; hint: string; connection: Connection | null };
type Form = { api_url: string; auth_type: Auth; username: string; header_name: string; secret: string };
const authLabels: Record<Auth, string> = { none: 'No authentication', bearer: 'Bearer token', basic: 'Username and password', api_key: 'API key header', graylog_token: 'Graylog access token' };

function initialForm(provider: Provider): Form {
  return {
    api_url: provider.connection?.api_url ?? '', auth_type: provider.connection?.auth_type ?? provider.default_auth,
    username: provider.connection?.username ?? '', header_name: provider.connection?.header_name || (provider.provider === 'logzio' ? 'X-API-TOKEN' : 'X-API-Key'), secret: '',
  };
}

function ConnectionForm({ provider, onChange }: { provider: Provider; onChange: (connection: Connection | null) => void }) {
  const [form, setForm] = useState(() => initialForm(provider));
  const [busy, setBusy] = useState<'save' | 'test' | 'remove'>();
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const credentialsUnchanged = provider.connection && form.api_url.trim().replace(/\/$/, '') === provider.connection.api_url
    && form.auth_type === provider.connection.auth_type
    && (form.auth_type !== 'basic' || form.username.trim() === provider.connection.username)
    && (form.auth_type !== 'api_key' || form.header_name === provider.connection.header_name);
  const canKeepSecret = credentialsUnchanged && provider.connection?.has_secret;

  function update<K extends keyof Form>(field: K, value: Form[K]) {
    setForm(current => ({ ...current, [field]: value })); setError(''); setMessage('');
  }

  async function change(action: 'save' | 'test' | 'remove') {
    setBusy(action); setError(''); setMessage('');
    try {
      const path = '/integrations/' + provider.provider;
      if (action === 'remove') {
        await request(path, { method: 'DELETE' });
        onChange(null); setForm(initialForm({ ...provider, connection: null }));
        setMessage('Connection removed.');
      } else if (action === 'save') {
        const connection = await request<Connection>(path, { method: 'PUT', body: JSON.stringify(form) });
        onChange(connection); setForm(initialForm({ ...provider, connection }));
        setMessage('Settings saved. Use Test connection to check API access.');
      } else {
        const result = await request<{ message: string }>(path + '/test', { method: 'POST', body: JSON.stringify(form) });
        setMessage(result.message + '.');
      }
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(undefined);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const action = (event.nativeEvent as SubmitEvent).submitter?.getAttribute('value');
    void change(action === 'test' ? 'test' : 'save');
  }

  return <form className="integration-form" onSubmit={submit} aria-label={provider.name + ' connection'}>
    <fieldset disabled={Boolean(busy)}>
      <legend>{provider.name} connection</legend>
      <p className="ops-muted text-sm">{provider.hint}</p>
      <div className="integration-fields">
        <label className="integration-field integration-field-wide">API base URL
          <input type="url" required maxLength={2048} value={form.api_url} onChange={event => update('api_url', event.target.value)} placeholder={provider.provider === 'opentelemetry' ? 'http://collector:4318' : 'https://monitoring.example.com'} autoComplete="off" />
        </label>
        <label className="integration-field">Authentication
          <select value={form.auth_type} onChange={event => { setForm(current => ({ ...current, auth_type: event.target.value as Auth, secret: '' })); setMessage(''); setError(''); }}>
            {provider.auth.map(auth => <option key={auth} value={auth}>{authLabels[auth]}</option>)}
          </select>
        </label>
        {form.auth_type === 'basic' && <label className="integration-field">Username
          <input required maxLength={256} value={form.username} onChange={event => update('username', event.target.value)} autoComplete="off" />
        </label>}
        {form.auth_type === 'api_key' && <label className="integration-field">API key header
          <input required readOnly={provider.provider === 'logzio'} maxLength={128} value={form.header_name} onChange={event => update('header_name', event.target.value)} autoComplete="off" />
        </label>}
        {form.auth_type !== 'none' && <label className="integration-field integration-field-wide">{form.auth_type === 'basic' ? 'Password' : 'API token or key'}
          <input type="password" required={!canKeepSecret} maxLength={4096} value={form.secret} onChange={event => update('secret', event.target.value)} autoComplete="new-password" placeholder={canKeepSecret ? 'Leave blank to keep the stored credential' : 'Enter credential'} />
          <span className="ops-muted">{canKeepSecret ? 'A credential is stored securely.' : 'Credentials are encrypted when saved.'}</span>
        </label>}
      </div>
      <p className="ops-muted text-xs">Connection checks run when you choose Test connection.</p>
      <div className="integration-actions">
        <button type="submit" value="save" className="ops-button">{busy === 'save' ? 'Saving…' : 'Save settings'}</button>
        <button type="submit" value="test" className="ops-button">{busy === 'test' ? 'Testing…' : 'Test connection'}</button>
        {provider.connection && <button type="button" className="ops-button" onClick={() => void change('remove')}><Trash2 size={14} aria-hidden="true" />{busy === 'remove' ? 'Removing…' : 'Remove connection'}</button>}
      </div>
    </fieldset>
    {error && <p className="ops-notice" role="alert">{error}</p>}
    {message && <p className="ops-success text-sm" role="status">{message}</p>}
  </form>;
}

export default function ApiIntegrations() {
  const [providers, setProviders] = useState<Provider[]>();
  const [selected, setSelected] = useState('grafana');
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError('');
    void request<Provider[]>('/integrations', { signal: controller.signal }).then(setProviders).catch(failure => {
      if (!controller.signal.aborted) setError(failure.message);
    });
    return () => controller.abort();
  }, [reload]);
  const current = providers?.find(provider => provider.provider === selected);
  return <div className="space-y-4">
    <section className="dash-panel" aria-labelledby="integrations-title">
      <header className="dash-panel-head"><div><h2 id="integrations-title"><Plug size={16} aria-hidden="true" /> API connections</h2>
        <p>Connect monitoring and logging services using their API credentials.</p></div></header>
      <div className="dash-panel-body">
        {error && <p role="alert" className="ops-notice">{error}<button type="button" className="ops-button" onClick={() => setReload(value => value + 1)}>Retry</button></p>}
        {!providers && !error && <p role="status" className="ops-muted">Loading API connections…</p>}
        {providers && <div className="integration-layout">
          <div className="integration-providers" role="group" aria-label="API providers">
            {providers.map(provider => <button key={provider.provider} type="button" className="integration-provider" aria-pressed={selected === provider.provider} onClick={() => setSelected(provider.provider)}>
              <span>{provider.name}</span><small>{provider.connection ? 'Settings saved' : 'Set up'}</small>
            </button>)}
          </div>
          {current && <ConnectionForm key={current.provider} provider={current} onChange={connection => setProviders(items => items?.map(item => item.provider === current.provider ? { ...item, connection } : item))} />}
        </div>}
      </div>
    </section>
    <ZabbixSettings />
  </div>;
}
