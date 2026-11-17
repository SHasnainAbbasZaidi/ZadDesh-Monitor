import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { Eye, EyeOff, Mail, RefreshCw } from 'lucide-react';
import { Card } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Modal } from '../components/ui/Modal';
import { useAuth } from '../context/AuthContext';
import { request } from '../services/api';

const endpoint = '/email-alerts';
const severities = ['Not Classified', 'Information', 'Warning', 'Average', 'High', 'Disaster'];
const categories = ['Network', 'Servers', 'Routers', 'Firewalls', 'CCTV', 'Other Devices'];
const problemTypes = { device_offline: 'Device offline', link_down: 'Link down', cpu_usage: 'CPU usage', memory_usage: 'Memory usage',
  network_usage: 'Network usage', storage_shortage: 'Storage shortage', interface_errors: 'Interface errors and packet loss',
  service_stopped: 'Service stopped', hardware: 'Hardware fault', vpn_failure: 'VPN failure', other: 'Other Zabbix problems' };
const templateKinds = ['critical', 'warning', 'recovery', 'escalation', 'reminder'] as const;
const sections = ['Email Server Settings', 'IT Employee Recipients', 'Alert Rules & Conditions', 'Email Templates', 'Notification History', 'Test & Diagnostics'];

type Schedule = { days: number[]; start: string; end: string };
type SMTP = { host: string; port: number; encryption: string; auth: string; username: string; sender: string; sender_name: string;
  reply_to: string | null; timeout: number; enabled: boolean; timezone: string; quiet_hours: Schedule | null; avoid_native: boolean };
type Employee = { id?: number; name: string; email: string; team: string; groups: string[]; enabled: boolean; severities: number[];
  host_group_ids: string[]; schedule: Schedule | null };
type Rule = { id?: number; name: string; enabled: boolean; severities: number[]; categories: string[]; host_ids: string[];
  host_group_ids: string[]; tags: Record<string, string>; problem_types: string[]; minimum_minutes: number; recipient_ids: number[];
  groups: string[]; schedule: Schedule | null; bypass_quiet: boolean; allow_duplicates: boolean; opened: boolean; recovery: boolean;
  escalate_severity: boolean; escalation_minutes: number | null; reminder_minutes: number[]; repeat_minutes: number | null };
type EventInput = { name: string; ip: string; category: string; problem: string; severity: number; problem_type: string;
  host_ids: string[]; host_group_ids: string[]; tags: Record<string, string>; duration_minutes: number };
type Template = { subject: string; text: string; html: string };
type Option = { id: string; name: string };
type Targets = { hosts: Option[]; groups: Option[] };
type Workspace = { server: { config: SMTP | null; has_credential: boolean; health: string; last_test: number | null;
  last_poll: number | null; last_error: string | null; poll_error: string | null }; recipients: Employee[]; rules: Rule[];
  templates: Record<typeof templateKinds[number], Template>; variables: string[]; zabbix_configured: boolean;
  stats: { recipients: number; rules: number; accepted_today: number; failed_attempts_today: number; pending: number } };
type Delivery = { id: number; address: string; device: string; ip: string; event_id: string; problem: string; kind: string; status: string;
  severity: number | null; created_at: number; sent_at: number | null; next_attempt: number; subject: string; attempts: number;
  last_error: string | null; uncertain: boolean; message?: Omit<Template, 'subject'>;
  attempt_history?: { at: number; status: string; outcome: string | null }[] };
type Notice = { text: string; error?: boolean };

const emptySMTP: SMTP = { host: '', port: 587, encryption: 'starttls', auth: 'none', username: '', sender: '', sender_name: 'ZadDesh Monitoring',
  reply_to: null, timeout: 10, enabled: false, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC', quiet_hours: null, avoid_native: false };
const emptyEmployee: Employee = { name: '', email: '', team: '', groups: [], enabled: true, severities: [], host_group_ids: [], schedule: null };
const emptyRule: Rule = { name: '', enabled: true, severities: [4, 5], categories: [], host_ids: [], host_group_ids: [], tags: {}, problem_types: [],
  minimum_minutes: 0, recipient_ids: [], groups: [], schedule: null, bypass_quiet: false, allow_duplicates: false, opened: true, recovery: true,
  escalate_severity: true, escalation_minutes: null, reminder_minutes: [], repeat_minutes: null };
const exampleEvent: EventInput = { name: 'Test device', ip: '', category: 'Network', problem: 'Simulated infrastructure outage', severity: 4,
  problem_type: 'device_offline', host_ids: [], host_group_ids: [], tags: {}, duration_minutes: 0 };

function call<T>(path: string, method: string, data?: unknown) {
  return request<T>(endpoint + path, { method, ...(data === undefined ? {} : { body: JSON.stringify(data) }) });
}
const message = (error: unknown) => error instanceof Error ? error.message : 'Request failed';
const split = (value: string) => value.split(',').map(part => part.trim()).filter(Boolean);
const pretty = (value: string) => value.replace(/_/g, ' ').replace(/^./, letter => letter.toUpperCase());
const stamp = (time: number | null, zone: string) => time == null ? '—' : new Date(time * 1000).toLocaleString(undefined, { timeZone: zone });
const deliveryLabel = (value: string) => value === 'sent' ? 'SMTP accepted' : pretty(value);

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="block min-w-0 text-sm font-medium"><span className="block mb-1">{label}</span>{children}</label>;
}
function Text({ label, value, change, type = 'text', required, placeholder }: {
  label: string; value: string | number; change: (value: string) => void; type?: string; required?: boolean; placeholder?: string;
}) {
  return <Field label={label}><input className="dash-select w-full" type={type} value={value} required={required} placeholder={placeholder} onChange={event => change(event.target.value)} /></Field>;
}
function NumberField({ label, value, change, min, max }: { label: string; value: number | null; change: (value: number | null) => void; min: number; max: number }) {
  return <Field label={label}><input className="dash-select w-full" type="number" min={min} max={max} value={value ?? ''}
    onChange={event => change(event.target.value === '' ? null : event.target.valueAsNumber)} /></Field>;
}
function Select({ label, value, change, options }: { label: string; value: string | number; change: (value: string) => void; options: Option[] }) {
  return <Field label={label}><select className="dash-select w-full" value={value} onChange={event => change(event.target.value)}>
    {options.map(option => <option key={option.id} value={option.id}>{option.name}</option>)}</select></Field>;
}
function Multiple({ label, value, change, options }: { label: string; value: string[]; change: (value: string[]) => void; options: Option[] }) {
  const missing = value.filter(id => !options.some(option => option.id === id)).map(id => ({ id, name: id }));
  const choices = [...options, ...missing];
  return <Field label={label}>{choices.length ? <select className="dash-select w-full" multiple size={Math.min(5, choices.length + 1)}
    value={value} onChange={event => change(Array.from(event.target.selectedOptions, item => item.value))}>
    {choices.map(option => <option key={option.id} value={option.id}>{option.name}</option>)}</select>
    : <span className="ops-muted block text-xs">No choices available</span>}</Field>;
}
function Toggle({ label, checked, change }: { label: string; checked: boolean; change: (value: boolean) => void }) {
  return <label className="inline-flex gap-2 items-center text-sm"><input type="checkbox" checked={checked} onChange={event => change(event.target.checked)} />{label}</label>;
}
function ScheduleFields({ title, value, change }: { title: string; value: Schedule | null; change: (schedule: Schedule | null) => void }) {
  return <fieldset className="space-y-2 border rounded-lg p-3" style={{ borderColor: 'var(--ops-line)' }}>
    <legend className="px-1 text-sm font-semibold">{title}</legend>
    <Toggle label="Use a schedule" checked={!!value} change={enabled => change(enabled ? { days: [0, 1, 2, 3, 4, 5, 6], start: '00:00', end: '00:00' } : null)} />
    {value && <><div className="flex flex-wrap gap-2">{['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((day, index) => <Toggle key={day} label={day}
      checked={value.days.includes(index)} change={enabled => change({ ...value, days: enabled ? [...value.days, index] : value.days.filter(item => item !== index) })} />)}</div>
      <div className="grid gap-2 sm:grid-cols-2"><Text label="From" type="time" value={value.start} change={start => change({ ...value, start })} required />
        <Text label="Until" type="time" value={value.end} change={end => change({ ...value, end })} required /></div>
      <p className="ops-muted text-xs">Uses the server time zone. Equal times select the whole day.</p></>}
  </fieldset>;
}
const severityOptions = severities.map((name, index) => ({ id: String(index), name }));
const categoryOptions = categories.map(name => ({ id: name, name }));
const typeOptions = Object.entries(problemTypes).map(([id, name]) => ({ id, name }));
const columns = 'grid gap-3 sm:grid-cols-2 lg:grid-cols-3';

function ServerForm({ state, save, test, busy }: { state: Workspace['server']; save: (data: unknown) => Promise<boolean>; test: () => void; busy: boolean }) {
  const [form, setForm] = useState(state.config ?? emptySMTP);
  const [secret, setSecret] = useState('');
  const [showSecret, setShowSecret] = useState(false);
  const canKeepSecret = state.has_credential && state.config?.host === form.host && state.config.port === form.port
    && state.config.encryption === form.encryption && state.config.auth === form.auth && state.config.username === form.username;
  const update = <K extends keyof SMTP>(key: K, value: SMTP[K]) => setForm(previous => ({ ...previous, [key]: value }));
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (await save({ ...form, reply_to: form.reply_to || null, ...(form.auth !== 'none' && secret ? { credential: secret } : {}) })) {
      setSecret(''); setShowSecret(false);
    }
  }
  return <Card title="Email Server Settings"><form className="space-y-4" onSubmit={submit}>
    <div className={columns}>
      <Text label="SMTP hostname or address" value={form.host} change={value => update('host', value)} required />
      <NumberField label="SMTP port" value={form.port} min={1} max={65535} change={value => update('port', value ?? 587)} />
      <Select label="Encryption" value={form.encryption} change={value => update('encryption', value)} options={[
        {id:'starttls', name:'STARTTLS'}, {id:'tls', name:'SSL/TLS'}, {id:'none', name:'None (unauthenticated relay)'}]} />
      <Select label="Authentication" value={form.auth} change={value => update('auth', value)} options={[
        {id:'none', name:'None'}, {id:'password', name:'Password or app password'}, {id:'oauth2', name:'OAuth2 access token (XOAUTH2)'}]} />
      {form.auth !== 'none' && <><Text label="SMTP username" value={form.username} change={value => update('username', value)} required />
        <div className="flex items-end gap-1"><div className="flex-1"><Field label={form.auth === 'oauth2' ? 'OAuth2 access token' : 'SMTP password'}>
          <input className="dash-select w-full" type={showSecret ? 'text' : 'password'} autoComplete="new-password" value={secret}
            placeholder={canKeepSecret ? 'Leave blank to keep saved credential' : ''}
            required={!canKeepSecret} onChange={event => setSecret(event.target.value)} /></Field></div>
          <button className="ops-button" type="button" aria-label={showSecret ? 'Hide credential' : 'Show credential'} onClick={() => setShowSecret(!showSecret)}>{showSecret ? <EyeOff size={16} /> : <Eye size={16} />}</button></div></>}
      <Text label="Sender email" type="email" value={form.sender} change={value => update('sender', value)} required />
      <Text label="Sender display name" value={form.sender_name} change={value => update('sender_name', value)} />
      <Text label="Reply-To email" type="email" value={form.reply_to ?? ''} change={value => update('reply_to', value)} />
      <NumberField label="Connection timeout (seconds)" value={form.timeout} min={2} max={20} change={value => update('timeout', value ?? 10)} />
      <Text label="Time zone" value={form.timezone} change={value => update('timezone', value)} required placeholder="Asia/Karachi" />
    </div>
    <ScheduleFields title="Quiet hours" value={form.quiet_hours} change={value => update('quiet_hours', value)} />
    <div className="flex flex-col gap-2">
      <Toggle label="Enable automatic Zabbix email notifications" checked={form.enabled} change={value => update('enabled', value)} />
      <Toggle label="Skip recipients already queued or notified by native Zabbix email" checked={form.avoid_native} change={value => update('avoid_native', value)} />
    </div>
    <p className="ops-muted text-xs">Tests use the saved server. OAuth2 accepts a provider-issued access token that you renew when it expires.</p>
    {state.last_error && <p role="status" className="ops-notice">{state.last_error}</p>}
    <div className="flex flex-wrap gap-2"><button type="submit" className="ops-button" disabled={busy}>Save SMTP settings</button>
      <button type="button" className="ops-button" disabled={busy || !state.config} onClick={test}>Test SMTP connection</button></div>
  </form></Card>;
}

function Directory({ recipients, targets, save, remove, busy, feedback }: { recipients: Employee[]; targets: Targets;
  save: (data: Employee) => Promise<boolean>; remove: (item: Employee) => void; busy: boolean; feedback?: string }) {
  const [draft, setDraft] = useState<Employee>();
  const [groups, setGroups] = useState('');
  function edit(item: Employee) { setGroups(item.groups.join(', ')); setDraft(item); }
  const update = <K extends keyof Employee>(key: K, value: Employee[K]) => setDraft(previous => previous && ({ ...previous, [key]: value }));
  return <Card title="IT Employee Recipients"><div className="space-y-3">
    <button type="button" className="ops-button" onClick={() => edit(emptyEmployee)}>Add recipient</button>
    {!recipients.length && <p className="ops-muted text-sm">Add employees before creating notification rules.</p>}
    <ul className="space-y-2">{recipients.map(item => <li key={item.id} className="dash-panel p-3 flex flex-wrap items-center gap-2 justify-between">
      <div><strong>{item.name}</strong> <Badge status={item.enabled ? 'up' : 'unknown'}>{item.enabled ? 'Enabled' : 'Disabled'}</Badge>
        <p className="ops-muted text-sm break-all">{item.email}{item.team && ` · ${item.team}`}{item.groups.length > 0 && ` · ${item.groups.join(', ')}`}</p></div>
      <div className="flex gap-2"><button type="button" className="ops-button" onClick={() => edit(item)}>Edit</button>
        <button type="button" className="ops-button" disabled={busy} onClick={() => remove(item)}>Remove</button></div>
    </li>)}</ul>
    {draft && <Modal isOpen wide title={draft.id ? 'Edit employee' : 'Add employee'} onClose={() => setDraft(undefined)}>
      <form className="space-y-4" onSubmit={async event => { event.preventDefault(); if (await save({ ...draft, groups: split(groups) })) setDraft(undefined); }}>
        {feedback && <p className="ops-notice" role="alert">{feedback}</p>}
        <div className={columns}>
          <Text label="Employee name" value={draft.name} change={value => update('name', value)} required />
          <Text label="Email address" type="email" value={draft.email} change={value => update('email', value)} required />
          <Text label="Department or team" value={draft.team} change={value => update('team', value)} />
          <Text label="Notification groups (comma separated)" value={groups} change={setGroups} />
          <Multiple label="Severities (empty means all)" value={draft.severities.map(String)} change={values => update('severities', values.map(Number))} options={severityOptions} />
          <Multiple label="Zabbix host groups (empty means all)" value={draft.host_group_ids} change={values => update('host_group_ids', values)} options={targets.groups} />
        </div>
        <Toggle label="Recipient enabled" checked={draft.enabled} change={value => update('enabled', value)} />
        <ScheduleFields title="Recipient availability" value={draft.schedule} change={value => update('schedule', value)} />
        <button type="submit" className="ops-button" disabled={busy}>Save recipient</button>
      </form>
    </Modal>}
  </div></Card>;
}

function tagText(tags: Record<string, string>) { return Object.entries(tags).map(([key, value]) => `${key}=${value}`).join('\n'); }
function parseTags(text: string) {
  return Object.fromEntries(text.split('\n').map(line => line.trim()).filter(Boolean).map(line => {
    const equals = line.indexOf('=');
    if (equals <= 0) throw new Error('Enter each Zabbix tag as name=value');
    return [line.slice(0, equals).trim(), line.slice(equals + 1).trim()];
  }));
}
function Tags({ value, change }: { value: string; change: (value: string) => void }) {
  return <Field label="Zabbix tags (one name=value per line)"><textarea className="dash-select w-full" rows={3} value={value} onChange={event => change(event.target.value)} /></Field>;
}

function Rules({ rules, recipients, targets, save, remove, busy, feedback }: { rules: Rule[]; recipients: Employee[]; targets: Targets;
  save: (rule: Rule) => Promise<boolean>; remove: (rule: Rule) => void; busy: boolean; feedback?: string }) {
  const [draft, setDraft] = useState<Rule>();
  const [tags, setTags] = useState('');
  const [reminders, setReminders] = useState('');
  const [error, setError] = useState('');
  function edit(rule: Rule) { setError(''); setTags(tagText(rule.tags)); setReminders(rule.reminder_minutes.join(', ')); setDraft(rule); }
  const update = <K extends keyof Rule>(key: K, value: Rule[K]) => setDraft(previous => previous && ({ ...previous, [key]: value }));
  const groups = [...new Set(recipients.flatMap(item => [item.team, ...item.groups]).filter(Boolean))].sort();
  return <Card title="Alert Rules & Conditions"><div className="space-y-3">
    <button type="button" className="ops-button" onClick={() => edit(emptyRule)}>Add rule</button>
    {!rules.length && <p className="ops-muted text-sm">No routing rules configured.</p>}
    <ul className="space-y-2">{rules.map(rule => <li key={rule.id} className="dash-panel p-3 flex flex-wrap items-center justify-between gap-2">
      <div><strong>{rule.name}</strong> <Badge status={rule.enabled ? 'up' : 'unknown'}>{rule.enabled ? 'Enabled' : 'Disabled'}</Badge>
        <p className="ops-muted text-sm">{rule.severities.map(level => severities[level]).join(', ')} · {rule.recipient_ids.length} employees · {rule.groups.length} groups</p></div>
      <div className="flex gap-2"><button type="button" className="ops-button" onClick={() => edit(rule)}>Edit</button>
        <button type="button" className="ops-button" disabled={busy} onClick={() => remove(rule)}>Remove</button></div>
    </li>)}</ul>
    {draft && <Modal isOpen wide title={draft.id ? 'Edit alert rule' : 'Add alert rule'} onClose={() => setDraft(undefined)}>
      <form className="space-y-4" onSubmit={async event => {
        event.preventDefault();
        try {
          const intervals = split(reminders).map(Number);
          if (intervals.some(value => !Number.isInteger(value) || value < 1)) throw new Error('Enter reminder intervals as positive whole minutes');
          setError('');
          if (await save({ ...draft, tags: parseTags(tags), reminder_minutes: intervals })) setDraft(undefined);
        } catch (failure) { setError(message(failure)); }
      }}>
        {(error || feedback) && <p role="alert" className="ops-notice">{error || feedback}</p>}
        <div className={columns}>
          <Text label="Rule name" value={draft.name} change={value => update('name', value)} required />
          <Multiple label="Severities" value={draft.severities.map(String)} change={values => update('severities', values.map(Number))} options={severityOptions} />
          <Multiple label="Device categories" value={draft.categories} change={values => update('categories', values)} options={categoryOptions} />
          <Multiple label="Zabbix hosts" value={draft.host_ids} change={values => update('host_ids', values)} options={targets.hosts} />
          <Multiple label="Zabbix host groups" value={draft.host_group_ids} change={values => update('host_group_ids', values)} options={targets.groups} />
          <Multiple label="Problem types" value={draft.problem_types} change={values => update('problem_types', values)} options={typeOptions} />
          <Tags value={tags} change={setTags} />
          <NumberField label="Minimum problem duration (minutes)" value={draft.minimum_minutes} change={value => update('minimum_minutes', value ?? 0)} min={0} max={10080} />
          <Multiple label="Employees" value={draft.recipient_ids.map(String)} change={values => update('recipient_ids', values.map(Number))}
            options={recipients.map(item => ({ id: String(item.id), name: `${item.name} (${item.email})` }))} />
          <Multiple label="Notification groups" value={draft.groups} change={values => update('groups', values)} options={groups.map(name => ({ id:name, name }))} />
          <Text label="Reminder minutes (comma separated)" value={reminders} change={setReminders} placeholder="15, 60" />
          <NumberField label="Repeat reminder every (minutes)" value={draft.repeat_minutes} change={value => update('repeat_minutes', value)} min={5} max={10080} />
          <NumberField label="Escalate after (minutes)" value={draft.escalation_minutes} change={value => update('escalation_minutes', value)} min={1} max={10080} />
        </div>
        <p className="ops-muted text-xs">Empty host, group, category and problem filters match all values.</p>
        <ScheduleFields title="Rule schedule" value={draft.schedule} change={value => update('schedule', value)} />
        <div className="flex flex-wrap gap-3">
          <Toggle label="Rule enabled" checked={draft.enabled} change={value => update('enabled', value)} />
          <Toggle label="Problem detected" checked={draft.opened} change={value => update('opened', value)} />
          <Toggle label="Problem recovered" checked={draft.recovery} change={value => update('recovery', value)} />
          <Toggle label="Severity increased" checked={draft.escalate_severity} change={value => update('escalate_severity', value)} />
          <Toggle label="High and Disaster bypass quiet hours" checked={draft.bypass_quiet} change={value => update('bypass_quiet', value)} />
          <Toggle label="Send separately when rules overlap" checked={draft.allow_duplicates} change={value => update('allow_duplicates', value)} />
        </div>
        <button type="submit" className="ops-button" disabled={busy}>Save alert rule</button>
      </form>
    </Modal>}
  </div></Card>;
}

function Message({ value }: { value: Template }) {
  return <article className="dash-panel p-3 space-y-2">
    <p className="text-sm font-semibold break-words">{value.subject}</p>
    {value.html && <iframe sandbox="" className="w-full min-h-44 rounded bg-white" title="HTML email preview" srcDoc={value.html} />}
    <details open={!value.html}><summary className="cursor-pointer text-sm">Plain text</summary><pre className="whitespace-pre-wrap break-words text-xs mt-2">{value.text}</pre></details>
  </article>;
}

function Templates({ data, save, busy }: { data: Workspace; save: (kind: string, template: Template) => Promise<boolean>; busy: boolean }) {
  const [kind, setKind] = useState<typeof templateKinds[number]>('critical');
  const [draft, setDraft] = useState(data.templates.critical);
  const [preview, setPreview] = useState<Template>();
  const [error, setError] = useState('');
  return <Card title="Email Templates"><form className="space-y-3" onSubmit={event => { event.preventDefault(); void save(kind, draft); }}>
    <Select label="Notification template" value={kind} change={value => { const next = value as typeof kind; setKind(next); setDraft(data.templates[next]); setPreview(undefined); setError(''); }}
      options={templateKinds.map(id => ({id, name:pretty(id)}))} />
    <p className="ops-muted text-xs">{data.variables.map(variable => `{{${variable}}}`).join(' · ')}</p>
    <Text label="Subject" value={draft.subject} change={subject => setDraft({ ...draft, subject })} required />
    <Field label="Plain-text body"><textarea className="dash-select w-full" rows={8} value={draft.text} onChange={event => setDraft({ ...draft, text: event.target.value })} required /></Field>
    <Field label="HTML body (optional)"><textarea className="dash-select w-full font-mono" rows={8} value={draft.html} onChange={event => setDraft({ ...draft, html: event.target.value })} /></Field>
    <p className="ops-muted text-xs">HTML accepts basic formatting tags without attributes.</p>
    <div className="flex flex-wrap gap-2"><button type="submit" className="ops-button" disabled={busy}>Save template</button>
      <button type="button" className="ops-button" onClick={async () => {
        try { setPreview(await call<Template>('/preview', 'POST', { event: exampleEvent, kind, template: draft })); setError(''); }
        catch (failure) { setError(message(failure)); }
      }}>Preview template</button></div>
    {error && <p className="ops-notice" role="alert">{error}</p>}{preview && <Message value={preview} />}
  </form></Card>;
}

function Diagnostics({ data, targets, busy, test, send }: { data: Workspace; targets: Targets; busy: boolean; test: () => void;
  send: (ids: number[], payload: EventInput) => Promise<boolean> }) {
  const [recipientIds, setRecipientIds] = useState<string[]>([]);
  const [event, setEvent] = useState(exampleEvent);
  const [tags, setTags] = useState('');
  const [preview, setPreview] = useState<Template>();
  const [routes, setRoutes] = useState<{ rule: string; recipients: { name: string; email: string; scheduled: boolean }[] }[]>();
  const [error, setError] = useState('');
  const patch = <K extends keyof EventInput>(key: K, value: EventInput[K]) => setEvent(previous => ({ ...previous, [key]: value }));
  async function run(action: 'preview' | 'simulate' | 'send') {
    try {
      setError(''); const payload = { ...event, tags: parseTags(tags) };
      if (action === 'send') await send(recipientIds.map(Number), payload);
      else if (action === 'preview') setPreview(await call<Template>('/preview', 'POST', { event: payload, kind: event.severity >= 4 ? 'critical' : 'warning' }));
      else setRoutes((await call<{ matches: NonNullable<typeof routes> }>('/simulate', 'POST', payload)).matches);
    } catch (failure) { setError(message(failure)); }
  }
  return <Card title="Test & Diagnostics"><div className="space-y-4">
    <p className="ops-muted text-sm">Test messages are marked TEST. Simulating a rule only evaluates routing.</p>
    <button type="button" className="ops-button" disabled={!data.server.config || busy} onClick={test}>Test SMTP connection</button>
    <p className="ops-muted text-xs">Last SMTP test {stamp(data.server.last_test, data.server.config?.timezone ?? 'UTC')} · Last Zabbix poll {stamp(data.server.last_poll, data.server.config?.timezone ?? 'UTC')}</p>
    {data.server.last_error && <p className="ops-notice">{data.server.last_error}</p>}
    {data.server.poll_error && <p className="ops-notice">{data.server.poll_error}</p>}
    <div className={columns}>
      <Text label="Simulated device" value={event.name} change={value => patch('name', value)} />
      <Text label="Device address" value={event.ip} change={value => patch('ip', value)} />
      <Text label="Problem description" value={event.problem} change={value => patch('problem', value)} />
      <Select label="Severity" value={event.severity} change={value => patch('severity', Number(value))} options={severityOptions} />
      <Select label="Category" value={event.category} change={value => patch('category', value)} options={categoryOptions} />
      <Select label="Problem type" value={event.problem_type} change={value => patch('problem_type', value)} options={typeOptions} />
      <NumberField label="Problem age (minutes)" value={event.duration_minutes} change={value => patch('duration_minutes', value ?? 0)} min={0} max={525600} />
      <Multiple label="Zabbix hosts" value={event.host_ids} change={values => patch('host_ids', values)} options={targets.hosts} />
      <Multiple label="Zabbix host groups" value={event.host_group_ids} change={values => patch('host_group_ids', values)} options={targets.groups} />
      <Tags value={tags} change={setTags} />
      <Multiple label="Test email recipients" value={recipientIds} change={setRecipientIds}
        options={data.recipients.filter(item => item.enabled).map(item => ({id:String(item.id), name:`${item.name} (${item.email})`}))} />
    </div>
    <div className="flex flex-wrap gap-2">
      <button type="button" className="ops-button" onClick={() => void run('preview')}>Preview test email</button>
      <button type="button" className="ops-button" onClick={() => void run('simulate')}>Simulate routing</button>
      <button type="button" className="ops-button" disabled={!recipientIds.length || !data.server.config || busy} onClick={() => void run('send')}>Send test email</button>
    </div>
    {error && <p role="alert" className="ops-notice">{error}</p>}
    {preview && <Message value={preview} />}
    {routes && <div className="dash-panel p-3 space-y-2"><h3 className="font-semibold">Routing results</h3>
      {routes.length ? routes.map((route, index) => <div key={`${route.rule}-${index}`}><strong>{route.rule}</strong>
        <ul className="text-sm">{route.recipients.length ? route.recipients.map(item => <li key={item.email}>{item.name} · {item.email} · {item.scheduled ? 'Eligible now' : 'Outside schedule'}</li>) : <li>No enabled matching recipients</li>}</ul>
      </div>) : <p className="ops-muted text-sm">No rules matched the simulated event.</p>}
    </div>}
  </div></Card>;
}

function History({ zone, revision, busy, retry }: { zone: string; revision: number; busy: boolean; retry: (delivery: Delivery) => Promise<boolean> }) {
  const [filter, setFilter] = useState({ q: '', status: '', severity: '', start: '', end: '' });
  const [query, setQuery] = useState('');
  const [page, setPage] = useState<{ items: Delivery[]; next: number | null }>();
  const [stack, setStack] = useState<number[]>([0]);
  const [detail, setDetail] = useState<Delivery>();
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    const params = new URLSearchParams(query);
    params.set('before', String(stack[stack.length - 1]));
    const load = () => request<{items:Delivery[]; next:number|null}>(`${endpoint}/history?${params}`, {signal:controller.signal})
      .then(data => { if (!controller.signal.aborted) { setPage(data); setError(''); } })
      .catch(failure => { if (!controller.signal.aborted) setError(message(failure)); })
      .finally(() => { if (!controller.signal.aborted) timer = window.setTimeout(load, 15000); });
    void load();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [query, stack, revision]);
  function apply(event: FormEvent) {
    event.preventDefault();
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filter)) {
      if (!value) continue;
      params.set(key, value);
    }
    setStack([0]); setQuery(params.toString());
  }
  async function exportCsv() {
    try {
      const token = localStorage.getItem('zaddesh_token');
      const response = await fetch(`/api/v1${endpoint}/history.csv?${query}`, {headers: { Authorization: `Bearer ${token ?? ''}` }});
      if (!response.ok) throw new Error(`CSV export failed (HTTP ${response.status})`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a'); link.href = url; link.download = 'zaddesh-email-history.csv'; link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (failure) { setError(message(failure)); }
  }
  return <Card title="Notification History"><div className="space-y-3">
    <form className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3" onSubmit={apply}>
      <Text label="Device, subject or email" value={filter.q} change={q => setFilter({ ...filter, q })} />
      <Select label="Delivery status" value={filter.status} change={status => setFilter({ ...filter, status })}
        options={[{id:'', name:'Any status'}, ...['queued', 'sending', 'sent', 'failed', 'retrying', 'cancelled'].map(id => ({id,name:deliveryLabel(id)}))]} />
      <Select label="Severity" value={filter.severity} change={severity => setFilter({ ...filter, severity })} options={[{id:'',name:'Any severity'}, ...severityOptions]} />
      <Text label="Start date" type="date" value={filter.start} change={start => setFilter({ ...filter, start })} />
      <Text label="End date" type="date" value={filter.end} change={end => setFilter({ ...filter, end })} />
      <div className="flex gap-2 items-end"><button type="submit" className="ops-button">Apply filters</button><button type="button" className="ops-button" onClick={() => void exportCsv()}>Export CSV</button></div>
    </form>
    <p className="ops-muted text-xs">SMTP accepted means the mail server accepted the message. Inbox delivery is unconfirmed.</p>
    {error && <p role="alert" className="ops-notice">{error}</p>}
    {!page && !error && <p role="status">Loading notification history…</p>}
    {page && !page.items.length && <p className="ops-muted">No notifications match these filters.</p>}
    {!!page?.items.length && <div className="overflow-x-auto"><table className="dash-table w-full">
      <thead><tr>{['Time', 'Device / Problem', 'Recipient', 'Severity', 'Type', 'Status', ''].map((label, index) => <th key={index} scope="col">{label}</th>)}</tr></thead>
      <tbody>{page.items.map(row => <tr key={row.id}>
        <td>{stamp(row.created_at, zone)}</td><td><strong>{row.device}</strong><p className="ops-muted">{row.ip} {row.problem}</p></td><td>{row.address}</td>
        <td>{row.severity == null ? '—' : severities[row.severity]}</td><td>{pretty(row.kind)}</td><td><Badge status={row.status === 'sent' ? 'up' : row.status === 'failed' ? 'down' : row.status === 'retrying' ? 'warning' : 'unknown'}>{deliveryLabel(row.status)}</Badge></td>
        <td><button type="button" className="ops-button" onClick={async () => {
          try { setDetail(await request<Delivery>(`${endpoint}/history/${row.id}`)); setError(''); } catch (failure) { setError(message(failure)); }
        }}>Details</button></td>
      </tr>)}</tbody>
    </table></div>}
    <div className="flex gap-2"><button type="button" className="ops-button" disabled={stack.length === 1} onClick={() => setStack(stack.slice(0, -1))}>Previous</button>
      <button type="button" className="ops-button" disabled={!page?.next} onClick={() => page?.next && setStack([...stack, page.next])}>Next</button></div>
    {detail && <Modal isOpen wide title="Notification details" onClose={() => setDetail(undefined)}>
      <div className="space-y-3"><p>{detail.address} · {deliveryLabel(detail.status)} · Zabbix event {detail.event_id}</p>
        <p className="ops-muted text-sm">{stamp(detail.created_at, zone)} · {detail.attempts} attempts{detail.sent_at ? ` · Accepted ${stamp(detail.sent_at, zone)}` : ''}</p>
        {detail.last_error && <p className="ops-notice">{detail.last_error}</p>}
        {detail.message && <Message value={{ subject: detail.subject, ...detail.message }} />}
        {!!detail.attempt_history?.length && <ol className="space-y-1 text-sm">{detail.attempt_history.map((attempt, index) => <li key={index}>{stamp(attempt.at, zone)} · {deliveryLabel(attempt.status)} {attempt.outcome && ` · ${attempt.outcome}`}</li>)}</ol>}
        {detail.status === 'failed' && <button type="button" className="ops-button" disabled={busy} onClick={async () => {
          if (await retry(detail)) setDetail(undefined);
        }}>Retry notification</button>}
      </div>
    </Modal>}
  </div></Card>;
}

export default function EmailAlerts() {
  const { user } = useAuth();
  const [panel, setPanel] = useState(0);
  const [data, setData] = useState<Workspace>();
  const [targets, setTargets] = useState<Targets>({hosts:[], groups:[]});
  const [targetError, setTargetError] = useState('');
  const [notice, setNotice] = useState<Notice>();
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [confirm, setConfirm] = useState<{ name: string; path: string }>();
  const admin = user?.role === 'admin';
  useEffect(() => {
    if (!admin) return;
    const controller = new AbortController();
    const refresh = () => request<Workspace>(endpoint, {signal: controller.signal})
      .then(value => { if (!controller.signal.aborted) setData(value); })
      .catch(error => { if (!controller.signal.aborted) setNotice({text:message(error), error:true}); });
    void refresh(); const timer = window.setInterval(refresh, 15000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [admin]);
  useEffect(() => {
    if (!admin || ![1, 2, 5].includes(panel)) return;
    const controller = new AbortController();
    request<Targets>(`${endpoint}/targets`, {signal:controller.signal})
      .then(value => { if (!controller.signal.aborted) { setTargets(value); setTargetError(''); } })
      .catch(() => { if (!controller.signal.aborted) setTargetError('Could not load Zabbix host and group choices.'); });
    return () => controller.abort();
  }, [admin, panel]);
  async function mutate(work: () => Promise<unknown>, success: string) {
    setBusy(true); setNotice(undefined);
    try {
      await work(); setNotice({text:success}); setRevision(value => value + 1);
      setData(await request<Workspace>(endpoint)); return true;
    } catch (error) { setNotice({text:message(error), error:true}); return false; }
    finally { setBusy(false); }
  }
  function test() {
    void mutate(async () => {
      const result = await call<{ok:boolean; message:string}>('/test-connection','POST');
      if (!result.ok) throw new Error(result.message);
    }, 'SMTP connection succeeded.');
  }
  if (!admin) return null;
  const active = data?.server.config?.enabled;
  const health = data?.server.health;
  const state = !data ? '—' : !active ? 'Disabled' : health === 'connected' ? 'Connected' : health?.includes('error') ? 'Error' : 'Untested';
  const metrics = [['Email server', state], ['Active recipients', data?.stats.recipients], ['Active rules', data?.stats.rules],
    ['SMTP accepted today', data?.stats.accepted_today], ['Failed attempts today', data?.stats.failed_attempts_today], ['Pending alerts', data?.stats.pending]];
  return <section className="space-y-4">
    <header className="flex flex-wrap justify-between items-center gap-3"><h2 className="text-xl font-semibold flex items-center gap-2"><Mail size={20} aria-hidden="true" /> Email Alerts &amp; Notifications</h2>
      <button type="button" className="ops-button" onClick={() => void mutate(() => Promise.resolve(), 'Notification workspace refreshed.')} disabled={busy}><RefreshCw size={15} /> Refresh</button></header>
    <dl className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2">{metrics.map(([label, value]) => <div key={label} className="dash-panel p-3"><dt className="ops-muted text-xs">{label}</dt><dd className="text-lg font-semibold">{value ?? '—'}</dd></div>)}</dl>
    <nav aria-label="Email notification sections" className="flex flex-wrap gap-2">{sections.map((label, index) => <button type="button" key={label}
      className="ops-button" aria-pressed={panel === index} style={panel === index ? { borderColor:'var(--ops-accent)' } : undefined}
      onClick={() => { setPanel(index); setNotice(undefined); }}>{label}</button>)}</nav>
    {notice && <p role={notice.error ? 'alert' : 'status'} className={notice.error ? 'ops-notice' : 'ops-success'}>{notice.text}</p>}
    {targetError && [1, 2, 5].includes(panel) && <p className="ops-muted text-sm" role="status">{targetError}</p>}
    {data && !data.zabbix_configured && <p className="ops-muted text-sm">Connect Zabbix in Settings to send automatic infrastructure notifications.</p>}
    {!data ? <p className="ops-muted" role="status">{notice?.error ? 'Email settings unavailable.' : 'Loading notification settings…'}</p> : <>
      {panel === 0 && <ServerForm state={data.server} busy={busy} test={test} save={value => mutate(() => call('/server','PUT',value), 'SMTP settings saved.')} />}
      {panel === 1 && <Directory recipients={data.recipients} targets={targets} busy={busy} feedback={notice?.error ? notice.text : undefined} save={({id, ...value}) => mutate(
        () => call(id ? `/recipients/${id}` : '/recipients', id ? 'PUT' : 'POST', value), 'Recipient saved.')}
        remove={item => setConfirm({name:item.name, path:`/recipients/${item.id}`})} />}
      {panel === 2 && <Rules rules={data.rules} recipients={data.recipients} targets={targets} busy={busy} feedback={notice?.error ? notice.text : undefined} save={({id, ...value}) => mutate(
        () => call(id ? `/rules/${id}` : '/rules', id ? 'PUT' : 'POST', value), 'Alert rule saved.')}
        remove={rule => setConfirm({name:rule.name, path:`/rules/${rule.id}`})} />}
      {panel === 3 && <Templates data={data} busy={busy} save={(kind, value) => mutate(() => call(`/templates/${kind}`,'PUT',value), 'Template saved.')} />}
      {panel === 4 && <History zone={data.server.config?.timezone ?? 'UTC'} revision={revision} busy={busy} retry={row => {
        if (row.uncertain && !window.confirm('Check SMTP logs first. Retry this message even if it may have been accepted?')) return Promise.resolve(false);
        return mutate(() => call(`/history/${row.id}/retry`,'POST',{confirm_uncertain:row.uncertain}), 'Notification queued for retry.');
      }} />}
      {panel === 5 && <Diagnostics data={data} targets={targets} busy={busy} test={test} send={(ids, value) => mutate(
        () => call('/test-email','POST',{recipient_ids:ids,event:value}), 'Test emails queued. Check Notification History for SMTP results.')} />}
    </>}
    {confirm && <Modal isOpen title="Confirm removal" onClose={() => setConfirm(undefined)}>
      <p>Remove {confirm.name}?</p><div className="flex gap-2 mt-4"><button type="button" className="ops-button" onClick={() => setConfirm(undefined)}>Cancel</button>
        <button type="button" className="ops-button" disabled={busy} onClick={async () => {
          if (await mutate(() => call(confirm.path,'DELETE'), 'Record removed.')) setConfirm(undefined);
        }}>Remove</button></div>
    </Modal>}
  </section>;
}
