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
  expect((await request.post('/api/v1/devices', { headers: auth, data: {
    name: 'Local validation switch', ip: '192.0.2.10', initial_check_types: ['icmp'],
  } })).status()).toBe(201);
  const url = 'https://zabbix.example.test/api_jsonrpc.php';
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
    } else if (path === 'hosts') {
      await route.fulfill({ json: { hosts: [{ hostid: '7', host: 'external-switch', name: 'External switch', status: '0',
        interfaces: [{ ip: '192.0.2.10', useip: '1' }] }], limited: false } });
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
  await page.getByLabel('Zabbix HTTPS URL').fill(url);
  const tokenField = page.getByLabel('Zabbix API token');
  await expect(tokenField).toHaveAttribute('type', 'password');
  await tokenField.fill(token);
  await page.getByRole('button', { name: 'Test and save' }).click();
  await expect(page.getByText('Zabbix accepted the token and allowed host, item and problem reads.')).toBeVisible();
  await expect(tokenField).toHaveValue('');

  await page.getByRole('button', { name: 'Devices', exact: true }).click();
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
