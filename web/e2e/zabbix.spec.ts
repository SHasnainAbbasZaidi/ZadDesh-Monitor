import { expect, test } from '@playwright/test';
import { randomBytes } from 'node:crypto';

test('Zabbix settings and read-only device view', async ({ page, request }) => {
  const password = randomBytes(20).toString('hex');
  const token = randomBytes(32).toString('hex');
  const setup = await request.post('/api/v1/auth/setup', {
    data: { username: 'zabbix-admin', email: 'zabbix-admin@example.com', password },
  });
  expect(setup.status()).toBe(201);
  const auth = { Authorization: `Bearer ${(await setup.json()).access_token}` };
  const localResponse = await request.post('/api/v1/devices', { headers: auth, data: {
    name: 'Local validation switch', ip: '192.0.2.10', initial_check_types: ['icmp'],
  } });
  expect(localResponse.status()).toBe(201);
  const local = await localResponse.json();
  expect((await request.post('/api/v1/devices', { headers: auth, data: {
    name: 'Local-only switch', ip: '192.0.2.11', initial_check_types: ['icmp'],
  } })).status()).toBe(201);
  const url = 'http://zabbix.example.test/api_jsonrpc.php';
  let saved = false;
  const timestamp = new Date().toISOString();
  await page.route('**/api/v1/zabbix/**', async route => {
    const path = new URL(route.request().url()).pathname.split('/').at(-1);
    if (path === 'connection') {
      if (route.request().method() === 'PUT') {
        expect(route.request().postDataJSON()).toEqual({ api_url: url, token });
        saved = true;
      }
      await route.fulfill({ json: { configured: saved, api_url: saved ? url : null, version: saved ? '7.4.1' : null } });
    } else if (path === 'inventory') {
      await route.fulfill({ json: { configured: true, hosts: [
        { hostid: '7', host: 'external-switch', name: 'External switch', status: '0', local_device_ids: [local.id],
          interfaces: [{ ip: '192.0.2.10', useip: '1', available: '1' }] },
        { hostid: '8', host: 'zabbix-only', name: 'Zabbix-only switch', status: '0', local_device_ids: [],
          interfaces: [{ ip: '192.0.2.12', useip: '1', available: '0' }] },
      ] } });
    } else if (path === 'hosts') {
      await route.fulfill({ json: { hosts: [{ hostid: '7', host: 'external-switch', name: 'External switch', status: '0',
        local_device_ids: [local.id], interfaces: [{ ip: '192.0.2.10', useip: '1' }] }], limited: false } });
    } else if (path === 'import') {
      expect(route.request().method()).toBe('POST');
      expect(new URL(route.request().url()).pathname).toBe('/api/v1/zabbix/hosts/8/import');
      await route.fulfill({ json: { status: 'added', device_id: 42, checks_created: 1,
        skipped: { 'SNMP credentials are hidden or use unresolved macros': 1 } } });
    } else if (path === 'problems') {
      await route.fulfill({ json: [{ eventid: '12', name: 'Link down on interface Gi0/1', severity: '4',
        clock: String(Math.floor(Date.now() / 1000)), acknowledged: '0' }] });
    } else if (path === 'validation') {
      await route.fulfill({ json: { status: 'match', zabbix: { up: true, timestamp },
        zaddesh: { up: true, timestamp }, skew_seconds: 0 } });
    } else {
      await route.abort();
    }
  });

  await page.goto('/');
  await page.getByLabel('Username').fill('zabbix-admin');
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Sign In', exact: true }).click();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByLabel('Zabbix URL').fill(url);
  await expect(page.getByText('HTTP sends the token unencrypted between ZadDesh and Zabbix.')).toBeVisible();
  const tokenField = page.getByLabel('Zabbix API token');
  await expect(tokenField).toHaveAttribute('type', 'password');
  await tokenField.fill(token);
  await page.getByRole('button', { name: 'Test and save' }).click();
  await expect(page.getByText('Zabbix accepted the token and allowed host, item and problem reads.')).toBeVisible();
  await expect(tokenField).toHaveValue('');

  await page.getByRole('button', { name: 'Devices', exact: true }).click();
  await expect(page.getByRole('row').filter({ hasText: 'External switch' })).toHaveCount(1);
  await expect(page.getByRole('row').filter({ hasText: 'Local validation switch' })).toHaveCount(0);
  await expect(page.getByRole('row').filter({ hasText: 'Local-only switch' })).toBeVisible();
  await expect(page.getByRole('row').filter({ hasText: 'Zabbix-only switch' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'External switch added to ZadDesh' })).toBeDisabled();
  await page.getByRole('button', { name: 'Add Zabbix-only switch to ZadDesh' }).click();
  await expect(page.getByRole('button', { name: 'Zabbix-only switch added to ZadDesh' })).toBeDisabled();
  await expect(page.getByText('1 check imported.')).toBeVisible();
  await expect(page.getByText(/1 skipped · SNMP credentials are hidden/)).toBeVisible();
  await page.getByRole('button', { name: 'Local telemetry for External switch' }).click();
  await expect(page.getByRole('dialog', { name: /Telemetry: Local validation switch/ })).toBeVisible();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Zabbix devices' }).click();
  await expect(page.getByRole('heading', { name: 'Zabbix hosts' })).toBeVisible();
  await expect(page.getByText('External switch', { exact: true }).first()).toBeVisible();
  await page.locator('summary').filter({ hasText: /^Link down$/ }).click();
  await expect(page.getByText('Link down on interface Gi0/1')).toBeVisible();
  await page.getByRole('combobox', { name: 'ZadDesh device', exact: true }).selectOption({ label: 'Local validation switch · 192.0.2.10' });
  await page.getByRole('combobox', { name: 'Zabbix host', exact: true }).selectOption('7');
  await page.getByRole('button', { name: 'Compare ICMP' }).click();
  await expect(page.getByText('Match', { exact: true })).toBeVisible();
});
