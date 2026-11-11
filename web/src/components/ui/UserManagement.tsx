import { useEffect, useState, type FormEvent } from 'react';
import { useAuth } from '../../context/AuthContext';
import { api } from '../../services/api';
import type { User } from '../../types';
import { Badge } from './Badge';
import { Card } from './Card';
import { Modal } from './Modal';

export function UserManagement() {
  const { user, logout } = useAuth();
  const [accounts, setAccounts] = useState<User[]>();
  const [editor, setEditor] = useState<User | 'new'>();
  const [error, setError] = useState('');
  const [formError, setFormError] = useState('');
  const [saving, setSaving] = useState(false);
  const [reload, setReload] = useState(0);
  const editing = editor && editor !== 'new' ? editor : undefined;
  const ownAccount = editing?.id === user?.id;

  useEffect(() => {
    let active = true;
    api.getUsers().then(result => {
      if (active) { setAccounts(result); setError(''); }
    }).catch(() => {
      if (active) setError('Could not load user accounts.');
    });
    return () => { active = false; };
  }, [reload]);

  function openEditor(account: User | 'new') {
    setFormError('');
    setEditor(account);
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = String(form.get('password') ?? '');
    if (password !== String(form.get('confirm_password') ?? '')) {
      setFormError('The passwords did not match.');
      return;
    }
    const role = form.get('role') === 'admin' ? 'admin' : 'viewer';
    setSaving(true);
    setFormError('');
    try {
      if (editing) {
        await api.updateUser(editing.id, {
          ...(ownAccount ? { current_password: String(form.get('current_password')) } : { role, is_active: form.has('is_active') }),
          password: password || undefined,
        });
        if (ownAccount) logout();
      } else {
        await api.createUser({
          username: String(form.get('username')).trim(), email: String(form.get('email')).trim(),
          full_name: String(form.get('full_name')).trim() || undefined, role, password,
        });
      }
      setEditor(undefined);
      setReload(value => value + 1);
    } catch (failure) {
      setFormError(failure instanceof Error ? failure.message : 'Could not save the account.');
    } finally {
      setSaving(false);
    }
  }

  return <>
    <Card title="Users and access">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <p className="ops-muted text-sm">Admins manage the app. Users can view Overview, Infrastructure Logs, and Alerts & Events.</p>
        <button className="ops-button" onClick={() => openEditor('new')}>Create user</button>
      </div>
      {error && <p role="alert" className="ops-notice">{error} <button className="underline" onClick={() => setReload(value => value + 1)}>Retry</button></p>}
      {!accounts && !error && <p role="status" className="ops-muted text-sm">Loading users…</p>}
      {!!accounts?.length && <div className="overflow-x-auto">
        <table className="dash-table w-full">
          <thead><tr>{['Account', 'Access', 'Status', 'Actions'].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead>
          <tbody>{accounts.map(account => <tr key={account.id}>
            <td><strong>{account.username}{account.id === user?.id ? ' (you)' : ''}</strong><span>{account.email}</span></td>
            <td>{account.role === 'admin' ? 'Admin' : 'User'}</td>
            <td><Badge status={account.is_active ? 'up' : 'unknown'}>{account.is_active ? 'Active' : 'Disabled'}</Badge></td>
            <td><button className="ops-button" aria-label={account.id === user?.id ? 'Change my password' : 'Edit ' + account.username}
              onClick={() => openEditor(account)}>{account.id === user?.id ? 'Change password' : 'Edit'}</button></td>
          </tr>)}</tbody>
        </table>
      </div>}
    </Card>
    <Modal isOpen={Boolean(editor)} onClose={() => { if (!saving) setEditor(undefined); }}
      title={ownAccount ? 'Change my password' : editing ? 'Edit ' + editing.username : 'Create user'}>
      {editor && <form key={editing?.id ?? 'new'} className="space-y-4 text-sm" onSubmit={save}>
        {formError && <p role="alert" className="ops-notice">{formError}</p>}
        {!editing && <>
          <label className="block">Username
            <input className="dash-select mt-1 block w-full" name="username" minLength={3} maxLength={64}
              pattern="[A-Za-z0-9_.\-]+" autoComplete="off" required />
          </label>
          <label className="block">Email address
            <input className="dash-select mt-1 block w-full" name="email" type="email" maxLength={254} required />
          </label>
          <label className="block">Full name (optional)
            <input className="dash-select mt-1 block w-full" name="full_name" maxLength={128} />
          </label>
        </>}
        {!ownAccount && <label className="block">Access level
          <select name="role" defaultValue={editing?.role ?? 'viewer'} className="dash-select mt-1 block w-full">
            <option value="viewer">User · read-only</option><option value="admin">Admin · full access</option>
          </select>
        </label>}
        {editing && !ownAccount && <label className="flex items-center gap-2">
          <input name="is_active" type="checkbox" defaultChecked={editing.is_active} /> Account enabled
        </label>}
        {ownAccount && <label className="block">Current password
          <input name="current_password" type="password" autoComplete="current-password" required maxLength={1024} className="dash-select mt-1 block w-full" />
        </label>}
        <label className="block">{editing ? 'New password' : 'Password'}{editing && !ownAccount ? ' (optional)' : ''}
          <input name="password" type="password" autoComplete="new-password" minLength={12} maxLength={1024}
            required={!editing || ownAccount} className="dash-select mt-1 block w-full" />
        </label>
        <label className="block">Confirm password
          <input name="confirm_password" type="password" autoComplete="new-password" maxLength={1024}
            required={!editing || ownAccount} className="dash-select mt-1 block w-full" />
        </label>
        <p className="ops-muted text-xs">Use at least 12 characters. Access changes require the account to sign in again.</p>
        <div className="flex justify-end gap-2">
          <button type="button" className="ops-button" disabled={saving} onClick={() => setEditor(undefined)}>Cancel</button>
          <button type="submit" className="ops-button" disabled={saving}>{saving ? 'Saving…' : editing ? 'Save changes' : 'Create account'}</button>
        </div>
      </form>}
    </Modal>
  </>;
}
