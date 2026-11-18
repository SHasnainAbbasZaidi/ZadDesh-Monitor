import { test, expect } from '@playwright/test';
import type { APIRequestContext } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { createServer } from 'node:http';
import { randomBytes } from 'node:crypto';
import { once } from 'node:events';
import { mkdir, readFile } from 'node:fs/promises';
import type { AddressInfo } from 'node:net';

const password = randomBytes(24).toString('hex');
const user = 'labadmin';
let token: string;
test.describe.configure({ mode: 'serial' });

async function ensureAdmin(request: APIRequestContext) {
  const status = await (await request.get('/api/v1/auth/status')).json();
  if (status.setup_required) {
    const response = await request.post('/api/v1/auth/setup', {
      data: { username: user, email: 'labadmin@example.com', password },
    });
    expect(response.status()).toBe(201);
  }
}

for (const viewport of [{ width: 390, height: 844 }, { width: 768, height: 1024 }, { width: 1440, height: 1000 }]) {
  test(`local HTTP target â†’ chart â†’ incident â†’ acknowledgement â†’ recovery at ${viewport.width}px`, async ({ page, request }, testInfo) => {
    const secret = process.env.ZADDESH_E2E_COLLECTOR;
    if (!secret) throw new Error('Set ZADDESH_E2E_COLLECTOR to the disposable serverâ€™s collector credential.');
    const start = performance.now();
    await page.setViewportSize(viewport);
    await page.goto('/');
    if (!token) {
      await page.getByLabel('Username').fill(user);
      await page.getByLabel('Email Address').fill('labadmin@example.com');
      await page.getByLabel('Password', { exact: true }).fill(password);
      await page.getByRole('button', { name: 'Complete Setup & Launch' }).click();
      await expect(page.getByRole('heading', { name: 'Infrastructure overview' })).toBeVisible();
      await page.getByRole('button', { name: 'Sign out' }).click();
    }
    await page.getByLabel('Username').fill(user);
    await page.getByLabel('Password', { exact: true }).fill(password);
    await page.getByRole('button', { name: 'Sign In', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Infrastructure overview' })).toBeVisible();
    token = await page.evaluate(() => localStorage.getItem('zaddesh_token') ?? '');
    const admin = { Authorization: `Bearer ${token}` };
    const collector = { Authorization: `Bearer ${secret}` };

    await page.getByRole('button', { name: 'Devices', exact: true }).click();
    await page.getByRole('button', { name: 'Add Device', exact: true }).click();
    const name = `Loopback HTTP ${viewport.width}`;
    await page.getByLabel('Device Name').fill(name);
    await page.getByLabel('IP Address').fill('127.0.0.1');
    await page.getByLabel('Priority alerts', { exact: true }).check();
    await page.getByText('ICMP Ping', { exact: true }).click();
    await page.getByText('HTTP', { exact: true }).click();
    await page.getByRole('dialog', { name: 'Add Monitored Device' }).getByText('SNMP', { exact: true }).click();
    await page.getByLabel('SNMP settings').selectOption('custom');
    const snmpVersion = viewport.width === 390 ? '1' : viewport.width === 768 ? '2c' : '3';
    await page.getByLabel('SNMP version').selectOption(snmpVersion);
    const credential = randomBytes(12).toString('hex');
    if (snmpVersion === '3') {
      await page.getByLabel('Hostname / FQDN').fill('localhost');
      await page.getByLabel('Connect to').selectOption('dns');
      await expect(page.getByLabel('Interface address')).toHaveValue('localhost');
      await page.getByLabel('Security name').fill('lab-user');
      await page.getByLabel('Authentication passphrase').fill(credential);
      await page.getByLabel('Privacy passphrase').fill(credential);
      await page.getByLabel('Context name').fill('lab');
      await page.getByLabel('Operation').selectOption('getbulk');
      await page.getByLabel('Max repetition count').fill('12');
    } else {
      await page.getByLabel('Community', { exact: true }).fill(credential);
    }
    const created = page.waitForResponse(r => r.url().endsWith('/api/v1/devices') && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Provision Device' }).click();
    const res = await created;
    expect(res.status()).toBe(201);
    const device = await res.json();
    const checks = await (await request.get(`/api/v1/checks?device_id=${device.id}`, { headers: admin })).json();
    const check = checks.find((value: { check_type: string }) => value.check_type === 'http');
    expect(check).toBeTruthy();
    expect(checks).toContainEqual(expect.objectContaining({
      check_type: 'snmp',
      target: snmpVersion === '3' ? 'localhost' : '127.0.0.1',
      params: expect.objectContaining({ version: snmpVersion, port: 161,
        ...(snmpVersion === '3' ? { context_name: 'lab', max_repetitions: 12 } : {}) }),
    }));
    expect(JSON.stringify(checks)).not.toContain(credential);

    // This is a real local socket. This test adapter submits samples through the collector API;
    // it does not exercise the Rust probe binary.
    let status = 200;
    const target = createServer((_req, res) => { res.writeHead(status).end('lab target'); });
    target.listen(0, '127.0.0.1');
    await once(target, 'listening');
    const address = target.address() as AddressInfo;
    const sample = async () => {
      const t = performance.now();
      let code = 0;
      try { code = (await fetch(`http://127.0.0.1:${address.port}/`)).status; } catch { /* closed target */ }
      const timestamp = new Date().toISOString();
      const points = [
        ['http.available', code === 200 ? 1 : 0],
        ['http.status_code', code],
        ['http.response_time_ms', performance.now() - t],
      ].map(([metric_key, value]) => ({ device_id: device.id, check_id: check.id, metric_key, value, timestamp }));
      const accepted = await request.post('/api/v1/metrics/ingest', { headers: collector, data: { points } });
      expect(accepted.status()).toBe(202);
    };

    try {
      await sample();
      await page.getByRole('button', { name, exact: true }).click();
      await expect(page.getByRole('dialog').locator('canvas').first()).toBeVisible();
      const firstChartMs = Math.round(performance.now() - start);
      await page.getByRole('button', { name: 'Close dialog' }).click();

      await page.getByRole('button', { name: 'Overview', exact: true }).click();
      await expect(page.getByText('Live updates connected')).toBeVisible();
      const summary = page.getByRole('region', { name: 'Infrastructure summary' });
      const fleetHealth = page.getByRole('region', { name: 'Fleet health' });
      const latency = page.getByRole('region', { name: 'Probe latency' });
      await expect(summary.getByRole('button')).toHaveCount(8);
      await expect(fleetHealth.getByText(/% healthy/)).toBeVisible();
      await expect(page.getByRole('region', { name: 'Devices by source' })).toBeVisible();
      await expect(page.getByRole('region', { name: 'Recent incidents by type' })).toBeVisible();
      const [summaryBox, latencyBox, fleetBox] = await Promise.all([summary.boundingBox(), latency.boundingBox(), fleetHealth.boundingBox()]);
      expect(latencyBox!.y).toBeGreaterThanOrEqual(summaryBox!.y + summaryBox!.height);
      if (viewport.width >= 1200) {
        expect(fleetBox!.y).toBe(latencyBox!.y);
        expect(fleetBox!.x).toBeGreaterThanOrEqual(latencyBox!.x + latencyBox!.width);
      } else {
        expect(fleetBox!.y).toBeGreaterThanOrEqual(latencyBox!.y + latencyBox!.height);
      }
      await latency.getByLabel('Latency protocol').selectOption('http');
      const chooser = latency.getByLabel('Latency device');
      await expect(chooser).toHaveValue('all');
      await expect(chooser.getByRole('option').first()).toHaveText('All priority devices');
      await expect(chooser.getByRole('option')).toHaveCount(viewport.width === 390 ? 2 : viewport.width === 768 ? 3 : 4);
      status = 503;
      const failureStart = performance.now();
      await sample();
      const failingProbes = latency.getByText('1 device failing HTTP(S) probes', { exact: true });
      await expect(failingProbes).toBeVisible();
      await expect(latency.getByRole('list', { name: 'Devices with outages' })).toBeHidden();
      await failingProbes.click();
      await latency.getByRole('list', { name: 'Devices with outages' }).getByRole('button', { name }).click();
      await expect(chooser).toHaveValue(String(device.id));
      await chooser.selectOption('all');
      await expect(latency.getByText('Failed probes', { exact: true })).toBeVisible();
      const failureCsv = page.waitForEvent('download');
      await latency.getByRole('button', { name: /Export .* CSV/ }).click();
      expect(await readFile(await (await failureCsv).path(), 'utf8')).toContain(`"${name}",,probe failed`);
      const events = page.getByRole('region', { name: 'Recent activity' });
      const event = events.locator('details').filter({ hasText: `Availability: ${check.name}` }).filter({ has: page.getByText('Probe down', { exact: true }) });
      await expect(event.getByText('Probe failed', { exact: true })).toBeHidden();
      const eventSummary = event.locator('summary');
      await eventSummary.focus();
      await eventSummary.press('Enter');
      await expect(event.getByText('Probe failed', { exact: true })).toBeVisible();
      await expect(eventSummary).toBeFocused();
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await eventSummary.press('Enter');
      await expect(event.getByText('Probe failed', { exact: true })).toBeHidden();
      await page.emulateMedia({ reducedMotion: 'no-preference' });
      await page.getByRole('button', { name: /Alerts & Events/ }).click();
      const alertTitle = page.getByText('Probe down', { exact: true });
      await expect(alertTitle).toBeVisible();
      await expect(page.getByText(`Availability: ${check.name}`, { exact: true })).toBeHidden();
      await alertTitle.click();
      await expect(page.getByText(`Availability: ${check.name}`, { exact: true })).toBeVisible();
      const incidentMs = Math.round(performance.now() - failureStart);
      const incident = page.locator('div').filter({ has: page.getByText(`Availability: ${check.name}`, { exact: true }) }).filter({ has: page.getByRole('button', { name: 'Acknowledge' }) });
      await incident.last().getByRole('button', { name: 'Acknowledge', exact: true }).click();
      const acknowledged = page.waitForResponse(r => r.url().endsWith('/acknowledge'));
      await page.getByRole('dialog').getByPlaceholder(/Investigating/).fill('Local audit probe');
      await page.getByRole('dialog').getByRole('button', { name: 'Confirm Acknowledgement' }).click();
      expect((await acknowledged).status()).toBe(200);
      status = 200;
      const recoveryStart = performance.now();
      await sample();
      const alerts = await (await request.get(`/api/v1/alerts?device_id=${device.id}`, { headers: admin })).json();
      expect(alerts[0].status).toBe('resolved');
      const recoveryMs = Math.round(performance.now() - recoveryStart);
      console.log(JSON.stringify({ viewport: viewport.width, firstChartMs, incidentMs, recoveryMs }));

      await page.getByRole('button', { name: 'Overview', exact: true }).click();
      await expect(page.getByRole('heading', { name: 'Infrastructure overview' })).toBeVisible();
      await expect(page.getByText('Live updates connected')).toBeVisible();
      await page.getByLabel('Telemetry time range').selectOption('15');
      await latency.getByLabel('Latency protocol').selectOption('http');
      await latency.getByLabel('Latency device').selectOption(String(device.id));
      await expect(latency.getByText('1 device · 1 reporting')).toBeVisible();
      await latency.getByLabel('Latency device').selectOption('all');
      await expect(latency.getByLabel('Latency device')).toHaveValue('all');
      await expect(latency.locator('canvas').first()).toBeVisible();
      await expect(page.getByRole('region', { name: 'Infrastructure Device Monitoring' }).getByRole('button', { name: new RegExp(name) })).toBeVisible();
      for (const theme of ['light', 'dark']) {
        await page.getByLabel('Color theme').selectOption(theme);
        const axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
        const serious = axe.violations.filter(v => v.impact === 'serious' || v.impact === 'critical');
        await testInfo.attach(`axe-${theme}`, { body: JSON.stringify(serious, null, 2), contentType: 'application/json' });
        expect(serious.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) }))).toEqual([]);
      }
      if (viewport.width === 1440 && process.env.ZADDESH_CAPTURE_DIR) {
        const path = process.env.ZADDESH_CAPTURE_DIR;
        await mkdir(path, { recursive: true });
        for (const theme of ['dark', 'light']) {
          await page.getByLabel('Color theme').selectOption(theme);
          for (const label of ['Overview', 'Devices', 'Checks', 'Alerts & Events', 'Settings']) {
            await page.getByRole('button', { name: new RegExp(`^${label}`) }).click();
            if (label === 'Overview') {
              await latency.getByLabel('Latency device').selectOption(String(device.id));
              await latency.getByLabel('Latency protocol').selectOption('http');
              await expect(latency.locator('canvas').first()).toBeVisible();
            }
            await page.waitForTimeout(300);
            await page.screenshot({ path: `${path}/after-${theme}-${label.split(' ')[0].toLowerCase()}.png` });
          }
        }
      }
      if (viewport.width === 1440) {
        await page.getByRole('button', { name: 'Checks', exact: true }).click();
        await page.getByRole('button', { name: 'SNMP', exact: true }).click();
        await expect(page.getByRole('row', { name: new RegExp(`${name} - SNMP`) })).toBeVisible();
        await page.getByRole('button', { name: 'Add SNMP Check' }).click();
        const dialog = page.getByRole('dialog', { name: 'Add SNMP Check' });
        await dialog.getByRole('combobox', { name: 'Device', exact: true }).selectOption(String(device.id));
        await dialog.getByLabel('SNMP version').selectOption('2c');
        await dialog.getByLabel('Operation').selectOption('getbulk');
        await dialog.getByLabel('Credential alias').fill('lab_switch');
        await expect(dialog.getByText('ZADDESH_SNMP_LAB_SWITCH_COMMUNITY')).toBeVisible();
        const createdSnmp = page.waitForResponse(response =>
          response.url().endsWith('/api/v1/checks') && response.request().method() === 'POST');
        await dialog.getByRole('button', { name: 'Create SNMP Check' }).click();
        const snmpResponse = await createdSnmp;
        expect(snmpResponse.status()).toBe(201);
        expect(await snmpResponse.json()).toMatchObject({
          device_id: device.id,
          check_type: 'snmp',
          params: { credential: 'lab_switch', version: '2c', operation: 'getbulk', port: 161 },
        });
        await expect(page.getByRole('row', { name: /SNMP uptime/ })).toBeVisible();
      }
    } finally {
      target.close();
    }
  });
}


test('download matching Windows and Linux SNMPv3 scripts and provision with only IP and port', async ({ page, request }) => {
  await ensureAdmin(request);
  await page.goto('/');
  await page.getByLabel('Username').fill(user);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Sign In', exact: true }).click();
  await page.getByRole('button', { name: 'Devices', exact: true }).click();
  await page.getByRole('button', { name: 'Add Device', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Add Monitored Device' });
  await dialog.getByLabel('IP Address').fill('192.0.2.10');
  await dialog.getByText('SNMP', { exact: true }).click();
  await expect(dialog.getByLabel('SNMP settings')).toHaveValue('default');
  await expect(dialog.getByLabel('Device Name')).toBeHidden();
  await dialog.getByLabel('UDP port').fill('1161');
  await expect(dialog.getByLabel('Authentication passphrase')).toHaveCount(0);

  const profiles: string[][] = [];
  for (const platform of ['Windows', 'Linux']) {
    const pendingDownload = page.waitForEvent('download');
    await dialog.getByRole('button', { name: `Download ${platform} setup` }).click();
    const download = await pendingDownload;
    expect(download.suggestedFilename()).toBe(platform === 'Windows' ? 'setup-windows-snmp.ps1' : 'setup-linux-snmp.sh');
    const script = await readFile(await download.path(), 'utf8');
    expect(script).toContain('1161');
    expect(script).toContain('rouser zaddesh priv');
    profiles.push(script.match(/[0-9a-f]{32}/g) ?? []);
  }
  expect(profiles[0]).toHaveLength(2);
  expect(profiles[0]).toEqual(profiles[1]);

  await page.evaluate(() => {
    URL.createObjectURL = () => { throw new Error('Browser downloads unavailable'); };
  });
  await dialog.getByRole('button', { name: 'Download Linux setup' }).click();
  await dialog.getByText('Download didn’t start? Copy setup script').click();
  await expect(dialog.getByLabel('Setup script contents')).toHaveValue(/agent_port=1161/);

  const created = page.waitForResponse(r => r.url().endsWith('/api/v1/devices') && r.request().method() === 'POST');
  await dialog.getByRole('button', { name: 'Provision Device' }).click();
  const response = await created;
  expect(response.status()).toBe(201);
  const device = await response.json();
  expect(device.name).toBe('192.0.2.10');
  const checks = await (await request.get(`/api/v1/checks?device_id=${device.id}`, {
    headers: { Authorization: `Bearer ${await page.evaluate(() => localStorage.getItem('zaddesh_token'))}` },
  })).json();
  expect(checks).toContainEqual(expect.objectContaining({
    check_type: 'snmp',
    params: expect.objectContaining({
      port: 1161, version: '3', security_level: 'authPriv', auth_protocol: 'sha1', privacy_protocol: 'aes128',
    }),
  }));
  expect(JSON.stringify(checks)).not.toContain(profiles[0][0]);
});

test('display and filter MongoDB-backed infrastructure logs', async ({ page, request }) => {
  const ingestToken = process.env.ZADDESH_E2E_LOG_TOKEN;
  test.skip(!ingestToken, 'Requires a disposable MongoDB log database');
  await ensureAdmin(request);
  const response = await request.post('/api/v1/logs/ingest', {
    headers: { Authorization: `Bearer ${ingestToken}` },
    data: { entries: [
      { source: 'lab-router', severity: 'warning', message: 'Link down on eth0 after exceeding the carrier loss threshold' },
      { source: 'lab-switch', severity: 'info', message: 'Lab switch ready' },
    ] },
  });
  expect(response.status()).toBe(202);
  await page.goto('/');
  await page.getByLabel('Username').fill(user);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Sign In', exact: true }).click();
  await page.getByRole('button', { name: 'Infrastructure Logs', exact: true }).click();
  await page.getByRole('button', { name: 'Integrated app logs', exact: true }).click();
  const warning = page.getByRole('row').filter({ hasText: 'lab-router' });
  await expect(warning.getByText('Link down', { exact: true })).toBeVisible();
  await expect(warning.getByText('Link down on eth0 after exceeding the carrier loss threshold', { exact: true })).toBeHidden();
  await warning.getByText('Link down', { exact: true }).click();
  await expect(warning.getByText('Link down on eth0 after exceeding the carrier loss threshold', { exact: true })).toBeVisible();
  await expect(page.getByRole('row').filter({ hasText: 'lab-switch' })).toBeVisible();
  await page.getByLabel('Severity').selectOption('warning');
  await page.getByRole('button', { name: 'Apply filters' }).click();
  await expect(warning.getByText('Link down', { exact: true })).toBeVisible();
  await expect(page.getByRole('row').filter({ hasText: 'lab-switch' })).toHaveCount(0);
});

test('Settings creates and updates a read-only user', async ({ page, request }) => {
  const username = 'ops-reader';
  const initial = randomBytes(16).toString('hex');
  const updated = randomBytes(16).toString('hex');
  await ensureAdmin(request);
  const ingestToken = process.env.ZADDESH_E2E_LOG_TOKEN;
  if (ingestToken) {
    const response = await request.post('/api/v1/logs/ingest', {
      headers: { Authorization: `Bearer ${ingestToken}` },
      data: { entries: [{ source: 'lab-settings', message: 'Settings access lab ready' }] },
    });
    expect(response.status()).toBe(202);
  }
  await page.goto('/');
  await page.getByLabel('Username').fill(user);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Sign In', exact: true }).click();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('button', { name: 'Create user', exact: true }).click();
  const create = page.getByRole('dialog', { name: 'Create user' });
  await create.getByLabel('Username').fill(username);
  await create.getByLabel('Email address').fill('ops-reader@example.com');
  await create.getByLabel('Access level').selectOption('viewer');
  await create.getByLabel('Password', { exact: true }).fill(initial);
  await create.getByLabel('Confirm password').fill(initial);
  await create.getByRole('button', { name: 'Create account' }).click();
  await page.getByRole('button', { name: 'Edit ' + username }).click();
  const edit = page.getByRole('dialog', { name: 'Edit ' + username });
  await edit.getByLabel('New password', { exact: false }).fill(updated);
  await edit.getByLabel('Confirm password').fill(updated);
  await edit.getByRole('button', { name: 'Save changes' }).click();
  await expect(edit).toBeHidden();
  expect((await request.post('/api/v1/auth/login', { data: { username, password: initial } })).status()).toBe(401);
  await page.getByRole('button', { name: 'Sign out' }).click();
  await page.getByLabel('Username').fill(username);
  await page.getByLabel('Password', { exact: true }).fill(updated);
  await page.getByRole('button', { name: 'Sign In', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Infrastructure overview' })).toBeVisible();
  const navigation = page.getByRole('navigation', { name: 'Main navigation' });
  await expect(navigation.getByRole('button')).toHaveCount(3);
  await expect(page.getByRole('button', { name: 'View devices' })).toHaveCount(0);
  await expect(page.getByRole('searchbox', { name: 'Search devices' })).toHaveCount(0);
  await navigation.getByRole('button', { name: 'Infrastructure Logs' }).click();
  await expect(page.getByRole('heading', { name: 'Infrastructure Logs' })).toBeVisible();
  if (ingestToken) {
    await page.getByRole('button', { name: 'Integrated app logs', exact: true }).click();
    await expect(page.getByRole('row').filter({ hasText: 'lab-settings' }).locator('summary')).toHaveText('Settings access lab ready');
  }
  await navigation.getByRole('button', { name: /Alerts & Events/ }).click();
  await expect(page.getByRole('heading', { name: 'Alerts & Incidents' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Acknowledge', exact: true })).toHaveCount(0);
});

test('SNMP workstations stay quiet until priority is selected, with process/service details and separated logs', async ({ page, request }) => {
  const auth = { Authorization: `Bearer ${token}` };
  const created = await request.post('/api/v1/devices', { headers: auth, data: {
    name: 'Validation workstation', ip: '192.0.2.80', initial_check_types: [], collection_mode: 'on_demand',
    initial_snmp: { params: { version: '3', credential: 'default' } },
  } });
  expect(created.status()).toBe(201);
  const device = await created.json();
  expect(device.is_priority).toBe(false);
  const workerToken = randomBytes(32).toString('hex');
  const enrollment = process.env.ZADDESH_E2E_ENROLLMENT;
  expect(enrollment).toBeTruthy();
  expect((await request.post('/api/v1/collectors/register', { headers: { Authorization: `Bearer ${enrollment}` }, data: {
    name: 'Browser inventory validation', auth_token: workerToken, version: 'validation', hostname: 'browser-lab',
  } })).status()).toBe(201);
  const worker = { Authorization: `Bearer ${workerToken}` };
  await page.addInitScript(value => localStorage.setItem('zaddesh_token', value), token);
  await page.goto('/');
  const nav = page.getByRole('navigation', { name: 'Main navigation' });
  await nav.getByRole('button', { name: 'Discovered Devices', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Discovered Devices' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Validation workstation', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'HTTP lab 1440', exact: true })).toHaveCount(0);
  const priority = page.getByRole('checkbox', { name: 'Priority alerts for Validation workstation', exact: true });
  await expect(priority).not.toBeChecked();
  await page.getByRole('button', { name: 'Validation workstation', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: /Telemetry: Validation workstation/ });
  await expect(dialog.getByText('Waiting for collector…')).toBeVisible();
  let job;
  for (let attempt = 0; attempt < 10; attempt++) {
    const next = await request.post('/api/v1/collectors/inventory-next', { headers: worker });
    expect(next.status()).toBe(200);
    job = await next.json();
    if (job.run_id === device.id) break;
    expect((await request.post(`/api/v1/collectors/inventory-result/${job.run_id}`, { headers: worker, data: {
      lease: job.lease, error: 'Earlier browser fixture has no network agent',
    } })).status()).toBe(200);
  }
  expect(job.run_id).toBe(device.id);
  const base = '1.3.6.1.4.1.8072.1.3.2.4.1.2.';
  const events = base + '14.122.97.100.100.101.115.104.95.101.118.101.110.116.115.';
  const services = base + '16.122.97.100.100.101.115.104.95.115.101.114.118.105.99.101.115.';
  const b64 = (value: string) => Buffer.from(value).toString('base64');
  expect((await request.post(`/api/v1/collectors/inventory-result/${device.id}`, { headers: worker, data: {
    lease: job.lease, values: {
      '1.3.6.1.2.1.1.5.0': 'validation-workstation',
      '1.3.6.1.2.1.25.4.2.1.2.42': 'validation-process', '1.3.6.1.2.1.25.4.2.1.7.42': '1',
      [services + '1']: 'Validation service\tRunning', [events + '1']: 'status|ready',
      [events + '2']: `${b64('browser-validation-event')}|${new Date().toISOString()}|warning|0|1|${b64('Storage warning\nValidation volume has low free space')}`,
    },
  } })).status()).toBe(200);
  await expect(dialog.getByRole('cell', { name: 'validation-process', exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: 'Services (1)' }).click();
  await expect(dialog.getByRole('cell', { name: 'Validation service', exact: true })).toBeVisible();
  await expect(dialog.getByText('Storage warning', { exact: true })).toBeVisible();
  const saved = page.waitForResponse(response => response.url().endsWith(`/api/v1/devices/${device.id}`) && response.request().method() === 'PUT');
  await dialog.getByRole('checkbox', { name: 'Priority alerts for Validation workstation', exact: true }).check();
  expect((await saved).status()).toBe(200);
  await expect(dialog.getByRole('checkbox', { name: 'Priority alerts for Validation workstation', exact: true })).toBeChecked();
  expect((await (await request.get(`/api/v1/devices/${device.id}`, { headers: auth })).json()).is_priority).toBe(true);
  for (const theme of ['light', 'dark']) {
    await page.evaluate(value => { localStorage.setItem('zaddesh_theme', value); document.documentElement.classList.toggle('dark', value === 'dark'); }, theme);
    await page.screenshot({ path: `../.audit/ui-polish/workstation-${theme}.png`, fullPage: true });
  }
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations.filter(item => ['critical', 'serious'].includes(item.impact ?? ''))).toEqual([]);
});
