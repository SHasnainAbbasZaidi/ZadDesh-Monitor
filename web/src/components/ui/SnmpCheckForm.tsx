import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api } from '../../services/api';
import type { Check, Device } from '../../types';
import windowsAgent from '../../agents/setup-windows-snmp.ps1?raw';
import linuxAgent from '../../agents/setup-linux-snmp.sh?raw';
import { includeEventLogInstaller } from '../../agents/includeEventLogInstaller';

const inputClass = 'mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-slate-600 dark:bg-slate-800';

function canEnterCredentials() {
  return window.location.protocol === 'https:' || ['127.0.0.1', 'localhost', '[::1]'].includes(window.location.hostname);
}

export function SnmpCheckForm({ onCreated }: { onCreated: (check: Check) => void }) {
  const [devices, setDevices] = useState<Device[]>();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    api.getDevices().then(result => {
      if (active) setDevices(result.filter(device => device.is_monitored));
    }).catch(() => {
      if (active) setError('Could not load devices. Close this form and try again.');
    });
    return () => { active = false; };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const device = devices?.find(item => item.id === Number(form.get('device')));
    if (!device) return;
    setSaving(true);
    setError('');
    try {
      const check = await api.createSnmpCheck({
        device_id: device.id,
        name: String(form.get('name')).trim(),
        target: device.ip,
        ...readSnmpConfig(form),
      });
      onCreated(check);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not save the SNMP check.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4 text-sm">
      {error && <p role="alert" className="ops-empty">{error}</p>}
      <label className="block">Device
        <select name="device" required defaultValue="" className={inputClass}>
          <option value="" disabled>{devices ? 'Select a monitored device' : 'Loading devices…'}</option>
          {devices?.map(device => <option key={device.id} value={device.id}>{device.name} ({device.ip})</option>)}
        </select>
      </label>
      {devices?.length === 0 && <p>Add a monitored device on the Devices page first.</p>}
      <label className="block">Check name
        <input name="name" defaultValue="SNMP uptime" required maxLength={128} className={inputClass} />
      </label>
      <SnmpFields />
      <button type="submit" className="ops-button" disabled={saving || !devices?.length}>
        {saving ? 'Saving…' : 'Create SNMP Check'}
      </button>
    </form>
  );
}


export function readSnmpConfig(form: FormData) {
  const version = String(form.get('snmp-version'));
  const security = String(form.get('snmp-security'));
  return {
    interval_seconds: Number(form.get('snmp-interval')),
    params: {
      credential: String(form.get('snmp-alias')),
      version,
      operation: String(form.get('snmp-operation')),
      oid: String(form.get('snmp-oid')),
      port: Number(form.get('snmp-port')),
      ...(form.has('snmp-repetitions') ? { max_repetitions: Number(form.get('snmp-repetitions')) } : {}),
      ...(form.get('snmp-context') ? { context_name: String(form.get('snmp-context')) } : {}),
      ...(version === '3' ? { security_level: security } : {}),
      ...(version === '3' && security !== 'noAuthNoPriv' ? { auth_protocol: String(form.get('snmp-auth')) } : {}),
      ...(version === '3' && security === 'authPriv' ? { privacy_protocol: String(form.get('snmp-privacy')) } : {}),
    },
  };
}

export function readSnmpInterface(form: FormData) {
  if (form.has('snmp-default') && !form.has('snmp-auth_password')) {
    throw new Error('Prepare a setup script for this IP and port before provisioning.');
  }
  const config = readSnmpConfig(form);
  const direct = form.get('snmp-credential-source') === 'direct';
  return {
    ...config,
    connect_to: form.get('snmp-connect-to'),
    params: { ...config.params, credential: direct ? undefined : config.params.credential },
    credentials: direct ? Object.fromEntries(
      ['community', 'username', 'auth_password', 'priv_password']
        .filter(field => form.has(`snmp-${field}`))
        .map(field => [field, String(form.get(`snmp-${field}`))])
    ) : undefined,
  };
}

function randomPassphrase() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
}

export function DefaultSnmpFields({ ip }: { ip: string }) {
  const [port, setPort] = useState('161');
  const [profile, setProfile] = useState<{ target: string; auth: string; privacy: string }>();
  const [prepared, setPrepared] = useState<{ filename: string; script: string; url?: string }>();
  const [copyStatus, setCopyStatus] = useState('');
  const [error, setError] = useState('');
  const scriptText = useRef<HTMLTextAreaElement>(null);
  const address = ip.trim();
  const target = `${address}:${Number(port)}`;
  const ready = profile?.target === target;
  const secure = canEnterCredentials();

  useEffect(() => {
    const url = prepared?.url;
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [prepared?.url]);

  function download(platform: 'windows' | 'linux') {
    const parts = address.split('.');
    if (parts.length !== 4 || parts.some(part => !/^(0|[1-9][0-9]{0,2})$/.test(part) || Number(part) > 255)
      || Number(parts[0]) === 0 || Number(parts[0]) >= 224) {
      setError('Enter the client’s IPv4 address.');
      return;
    }
    const udpPort = Number(port);
    if (!Number.isInteger(udpPort) || udpPort < 1 || udpPort > 65535) {
      setError('Enter a UDP port from 1 to 65535.');
      return;
    }
    const selected = ready && profile ? profile : { target, auth: randomPassphrase(), privacy: randomPassphrase() };
    const windows = platform === 'windows';
    const profileText = windows
      ? `$agentPort = ${udpPort}\n$agentAuth = '${selected.auth}'\n$agentPrivacy = '${selected.privacy}'`
      : `agent_port=${udpPort}\nlisten_ip='${address}'\nauth='${selected.auth}'\nprivacy='${selected.privacy}'`;
    const script = includeEventLogInstaller(windows ? windowsAgent : linuxAgent, windows ? 'windows' : 'linux').replace('# ZADDESH GENERATED PROFILE', profileText);
    const filename = windows ? 'setup-windows-snmp.ps1' : 'setup-linux-snmp.sh';
    let url: string | undefined;
    try {
      url = URL.createObjectURL(new Blob([script], { type: 'application/octet-stream' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      link.hidden = true;
      document.body.append(link);
      try { link.click(); } finally { link.remove(); }
    } catch {
      // The copy option remains available when the browser blocks file downloads.
    }
    setPrepared({ filename, script, url });
    setProfile(selected);
    setCopyStatus('');
    setError('');
  }

  async function copyScript() {
    if (!prepared) return;
    try {
      await navigator.clipboard.writeText(prepared.script);
      setCopyStatus('Script copied.');
    } catch {
      scriptText.current?.focus();
      scriptText.current?.select();
      setCopyStatus('Script selected. Press Ctrl+C or Command+C to copy.');
    }
  }

  return <div className="space-y-3 text-sm">
    <label className="block">UDP port
      <input name="snmp-port" type="number" min={1} max={65535} value={port}
        onChange={event => setPort(event.target.value)} required className={inputClass} />
    </label>
    <input type="hidden" name="snmp-default" value="true" />
    <input type="hidden" name="snmp-connect-to" value="ip" />
    <input type="hidden" name="snmp-credential-source" value="direct" />
    <input type="hidden" name="snmp-version" value="3" />
    <input type="hidden" name="snmp-security" value="authPriv" />
    <input type="hidden" name="snmp-auth" value="sha1" />
    <input type="hidden" name="snmp-privacy" value="aes128" />
    <input type="hidden" name="snmp-operation" value="get" />
    <input type="hidden" name="snmp-oid" value="1.3.6.1.2.1.1.3.0" />
    <input type="hidden" name="snmp-interval" value="60" />
    <input type="hidden" name="snmp-username" value="zaddesh" />
    {ready && <>
      <input type="hidden" name="snmp-auth_password" value={profile.auth} />
      <input type="hidden" name="snmp-priv_password" value={profile.privacy} />
    </>}
    {secure ? <>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="ops-button" onClick={() => download('windows')}>Download Windows setup</button>
        <button type="button" className="ops-button" onClick={() => download('linux')}>Download Linux setup</button>
      </div>
      {ready && prepared && <details className="entry-details rounded-lg border border-slate-500/30 p-3">
        <summary>Download didn’t start? Copy setup script</summary>
        <div className="mt-2 space-y-2">
          <p>Save the script as <code>{prepared.filename}</code> on the client. It contains this device’s credentials.</p>
          <div className="flex flex-wrap items-center gap-2">
            {prepared.url && <a className="ops-button" href={prepared.url} download={prepared.filename}>Download again</a>}
            <button type="button" className="ops-button" onClick={() => void copyScript()}>Copy script</button>
            {copyStatus && <span role="status">{copyStatus}</span>}
          </div>
          <textarea ref={scriptText} aria-label="Setup script contents" readOnly value={prepared.script}
            rows={6} className="dash-select block w-full font-mono text-xs" />
        </div>
      </details>}
      <p>Run the setup script on the client, then provision this device. Keep the script private and delete it after setup.</p>
      <p>Windows requires a compatible Net-SNMP build. Run the script in an Administrator terminal and enter the collector IPv4 address.</p>
      <code className="block break-all">powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup-windows-snmp.ps1</code>
      <p>Linux requires Debian or Ubuntu with systemd. Allow the configured UDP port from the collector.</p>
      <code className="block">sudo bash ./setup-linux-snmp.sh</code>
    </> : <p role="status">Open ZadDesh over HTTPS to generate a setup script, or use Custom SNMP settings with a collector alias.</p>}
    {error && <p role="alert">{error}</p>}
  </div>;
}

export function SnmpFields({ deviceInterface }: { deviceInterface?: { ip: string; hostname: string } }) {
  const [version, setVersion] = useState('3');
  const [security, setSecurity] = useState('authPriv');
  const [operation, setOperation] = useState('get');
  const [port, setPort] = useState('161');
  const [connectTo, setConnectTo] = useState('ip');
  const [source, setSource] = useState(() => canEnterCredentials() ? 'direct' : 'alias');
  const [alias, setAlias] = useState('');
  const direct = Boolean(deviceInterface) && source === 'direct';
  const envPrefix = 'ZADDESH_SNMP_' + (alias.toUpperCase() || 'MY_ALIAS');
  const credentials = version === '3'
    ? ['USERNAME', ...(security === 'noAuthNoPriv' ? [] : ['AUTH_PASSWORD']), ...(security === 'authPriv' ? ['PRIV_PASSWORD'] : [])]
    : ['COMMUNITY'];

  return (
    <div className="space-y-4 text-sm">
      <div className="grid gap-3 sm:grid-cols-3">
        {deviceInterface && <>
          <label>Connect to
            <select name="snmp-connect-to" className={inputClass} value={connectTo} onChange={event => setConnectTo(event.target.value)}>
              <option value="ip">IP address</option>
              <option value="dns" disabled={!deviceInterface.hostname.trim()}>DNS name</option>
            </select>
          </label>
          <label>Interface address
            <input readOnly value={(connectTo === 'dns' ? deviceInterface.hostname : deviceInterface.ip).trim()} placeholder={connectTo === 'dns' ? 'Enter the hostname above' : 'Enter the IP address above'} className={inputClass} />
          </label>
        </>}
        <label>UDP port
          <input name="snmp-port" type="number" min={1} max={65535} value={port}
            onChange={event => setPort(event.target.value)} required className={inputClass} />
        </label>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label>SNMP version
          <select name="snmp-version" className={inputClass} value={version} onChange={event => {
            setVersion(event.target.value);
            if (event.target.value === '1' && operation === 'getbulk') setOperation('get');
          }}>
            <option value="3">SNMPv3</option><option value="2c">SNMPv2c</option><option value="1">SNMPv1</option>
          </select>
        </label>
        {deviceInterface && <label>Credential source
          <select name="snmp-credential-source" className={inputClass} value={source} onChange={event => setSource(event.target.value)}>
            <option value="direct" disabled={!canEnterCredentials()}>Enter credentials</option><option value="alias">Collector alias</option>
          </select>
          {!canEnterCredentials() && <span className="text-xs">Open ZadDesh over HTTPS to enter credentials here.</span>}
        </label>}
      </div>
      {version === '3' && (
        <div className="grid gap-3 sm:grid-cols-2">
          <label>Context name
            <input name="snmp-context" maxLength={32} placeholder="Optional" className={inputClass} />
          </label>
          {direct && <label>Security name
            <input name="snmp-username" autoComplete="off" maxLength={32} required className={inputClass} />
          </label>}
          <label>Security level
            <select name="snmp-security" className={inputClass} value={security} onChange={event => setSecurity(event.target.value)}>
              <option value="authPriv">authPriv · Authentication and privacy</option>
              <option value="authNoPriv">authNoPriv · Authentication only</option>
              <option value="noAuthNoPriv">noAuthNoPriv · No authentication or privacy</option>
            </select>
          </label>
          {security !== 'noAuthNoPriv' && <label>Authentication protocol
            <select name="snmp-auth" defaultValue="sha256" className={inputClass}>
              {['sha256', 'sha1', 'sha224', 'sha384', 'sha512', 'md5'].map(value => <option key={value}>{value}</option>)}
            </select>
          </label>}
          {direct && security !== 'noAuthNoPriv' && <label>Authentication passphrase
            <input name="snmp-auth_password" type="password" autoComplete="new-password" minLength={8} maxLength={255} required className={inputClass} />
          </label>}
          {security === 'authPriv' && <label>Privacy protocol
            <select name="snmp-privacy" className={inputClass}>
              {['aes128', 'aes192', 'aes256', 'des'].map(value => <option key={value}>{value}</option>)}
            </select>
          </label>}
          {direct && security === 'authPriv' && <label>Privacy passphrase
            <input name="snmp-priv_password" type="password" autoComplete="new-password" minLength={8} maxLength={255} required className={inputClass} />
          </label>}
        </div>
      )}
      {direct && version !== '3' && <label className="block">Community
        <input key={version} name="snmp-community" type="password" autoComplete="new-password" maxLength={255} required className={inputClass} />
      </label>}
      {!direct && <><label className="block">Credential alias
        <input name="snmp-alias" value={alias} onChange={event => setAlias(event.target.value)} pattern="[A-Za-z0-9_]+" maxLength={48} placeholder="switch_a" required className={inputClass} />
      </label>
      <p className="text-slate-600 dark:text-slate-300">
        Set these variables in the collector’s private environment before starting it. The dashboard stores only the alias.
      </p>
      <ul className="list-inside list-disc break-all">{credentials.map(key => <li key={key}><code>{envPrefix}_{key}</code></li>)}</ul></>}
      <div className="grid gap-3 border-t border-slate-200 pt-4 dark:border-slate-700 sm:grid-cols-2">
        <label>Operation
          <select name="snmp-operation" className={inputClass} value={operation} onChange={event => setOperation(event.target.value)}>
            <option value="get">GET</option><option value="getnext">GETNEXT</option>
            {version !== '1' && <option value="getbulk">GETBULK</option>}
            <option value="walk">WALK</option>
          </select>
        </label>
        <label>Numeric OID
          <input name="snmp-oid" defaultValue="1.3.6.1.2.1.1.3.0" pattern="[.]?[0-9]+([.][0-9]+)+" maxLength={123} required className={inputClass} />
        </label>
        {operation === 'getbulk' && <label>Max repetition count
          <input name="snmp-repetitions" type="number" min={1} max={64} defaultValue={10} required className={inputClass} />
        </label>}
        <label>Poll interval (seconds)
          <input name="snmp-interval" type="number" min={5} max={86400} defaultValue={60} required className={inputClass} />
        </label>
      </div>
    </div>
  );
}
