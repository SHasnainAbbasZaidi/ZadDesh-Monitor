import { expect, test } from '@playwright/test';
import { randomBytes } from 'node:crypto';
import { mkdir } from 'node:fs/promises';

test('add-on workspace and published diagram access', async ({ page, request }) => {
  test.setTimeout(90_000);
  const featureResponse = await request.get('/api/v1/features');
  const features = await featureResponse.json();
  test.skip(!features.incident_workspace || !features.network_diagram_beta, 'Requires both add-on flags');
  const password = randomBytes(16).toString('hex');
  const setup = await request.post('/api/v1/auth/setup', {
    data: { username: 'addon-admin', email: 'addon-admin@example.com', password },
  });
  expect(setup.status()).toBe(201);
  const admin = { Authorization: `Bearer ${(await setup.json()).access_token}` };
  const deviceResponse = await request.post('/api/v1/devices', { headers: admin,
    data: { name: 'Add-on fixture', ip: '192.0.2.10', initial_check_types: ['http'] },
  });
  expect(deviceResponse.status()).toBe(201);
  const device = await deviceResponse.json();
  const [check] = await (await request.get(`/api/v1/checks?device_id=${device.id}`, { headers: admin })).json();
  const timestamp = new Date(Date.now() - 2000).toISOString();
  const sample = await request.post('/api/v1/metrics/ingest', {
    headers: { Authorization: `Bearer ${process.env.ZADDESH_E2E_COLLECTOR}` },
    data: { points: [['http.available', 0], ['http.response_time_ms', 28]].map(([metric_key, value]) =>
      ({ device_id: device.id, check_id: check.id, metric_key, value, timestamp })) },
  });
  expect(sample.status()).toBe(202);
  const logToken = process.env.ZADDESH_E2E_LOG_TOKEN;
  if (logToken) {
    expect((await request.post('/api/v1/logs/ingest', {
      headers: { Authorization: `Bearer ${logToken}` },
      data: { entries: [{ source: device.ip, message: 'Link down password=fixture-credential', severity: 'warning' }] },
    })).status()).toBe(202);
  }

  async function signIn(username: string) {
    await page.getByLabel('Username').fill(username);
    await page.getByLabel('Password', { exact: true }).fill(password);
    await page.getByRole('button', { name: 'Sign In', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Infrastructure overview' })).toBeVisible();
  }
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto('/');
  await signIn('addon-admin');
  const socket = page.waitForEvent('websocket', { predicate: item => item.url().endsWith('/addon-updates') });
  await page.getByRole('button', { name: /Alerts & Events/ }).click();
  await socket;
  await expect(page.getByRole('heading', { name: 'Alerts, events and logs' })).toBeVisible();
  await page.getByRole('button', { name: /Probe down · Add-on fixture/ }).click();
  await expect(page.getByRole('heading', { name: 'Timeline' })).toBeVisible();
  await expect(page.locator('canvas').first()).toBeVisible();
  const capture = process.env.ZADDESH_CAPTURE_DIR;
  if (capture) await mkdir(capture, { recursive: true });
  for (const theme of ['light', 'dark']) {
    await page.getByLabel('Color theme').selectOption(theme);
    if (capture) await page.screenshot({ path: `${capture}/incident-${theme}-desktop.png`, fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole('dialog')).toBeVisible();
    if (capture) await page.screenshot({ path: `${capture}/incident-${theme}-mobile.png`, fullPage: true });
    await page.setViewportSize({ width: 1440, height: 960 });
    await expect(page.getByRole('dialog')).toHaveCount(0);
  }
  await page.getByRole('button', { name: 'errors', exact: true }).click();
  await page.locator('summary').filter({ hasText: /^Probe down$/ }).click();
  await expect(page.locator('.entry-detail-body > p').filter({ hasText: /Probe failed/ })).toBeVisible();
  await page.getByRole('button', { name: 'events', exact: true }).click();
  await expect(page.locator('summary').filter({ hasText: /^Probe down$/ })).toBeVisible();
  if (logToken) {
    await page.getByRole('button', { name: 'logs', exact: true }).click();
    await page.locator('summary').filter({ hasText: /^Link down$/ }).click();
    await expect(page.getByText('Link down password=[redacted]', { exact: true })).toBeVisible();
    await expect(page.getByText('fixture-credential', { exact: false })).toHaveCount(0);
    await page.getByRole('button', { name: 'Infrastructure Logs' }).click();
    await expect(page.getByRole('button', { name: 'logs', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('summary').filter({ hasText: /^Link down$/ })).toBeVisible();
  }
  await page.getByRole('button', { name: 'Network Diagram Beta' }).click();
  await page.getByLabel('New diagram name').fill('Published lab diagram');
  await page.getByRole('button', { name: 'Create diagram', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Published lab diagram' })).toBeVisible();
  const diagrams = await (await request.get('/api/v1/network-diagram', { headers: admin })).json();
  const id = diagrams[0].id;
  for (const label of ['Fixture switch', 'Fixture router']) {
    expect((await request.put(`/api/v1/network-diagram/${id}/nodes/0`, {
      headers: admin, data: { label, kind: label.endsWith('router') ? 'router' : 'switch' },
    })).status()).toBe(204);
  }
  const graph = await (await request.get(`/api/v1/network-diagram/${id}`, { headers: admin })).json();
  expect((await request.put(`/api/v1/network-diagram/${id}/links/0`, { headers: admin,
    data: { source: graph.nodes[0].id, target: graph.nodes[1].id, source_port: 'Gi0/1', target_port: 'Gi0/2' },
  })).status()).toBe(204);
  await expect(page.getByRole('img', { name: 'Network diagram with 2 nodes and 1 links' })).toBeVisible();
  for (const theme of ['light', 'dark']) {
    await page.getByLabel('Color theme').selectOption(theme);
    if (capture) await page.screenshot({ path: `${capture}/diagram-${theme}.png`, fullPage: true });
  }
  const viewerResponse = await request.post('/api/v1/auth/users', { headers: admin,
    data: { username: 'addon-viewer', email: 'addon-viewer@example.com', password, role: 'viewer' },
  });
  expect(viewerResponse.status()).toBe(201);
  await page.getByRole('button', { name: 'Publish', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Unpublish', exact: true })).toBeVisible();
  if (process.env.ZADDESH_E2E_AUTO_TOPOLOGY) {
    const saved = await request.post('/api/v1/devices', { headers: admin, data: {
      name: 'Automatic topology seed', ip: '192.0.2.23', initial_check_types: [],
      initial_snmp: { params: { version: '3', security_level: 'authNoPriv' },
        credentials: { username: 'topology-fixture', auth_password: randomBytes(16).toString('hex') } },
    } });
    expect(saved.status()).toBe(201);
    await expect(page.getByRole('combobox', { name: 'Diagram', exact: true }).locator('option')
      .filter({ hasText: 'Automatic network map' })).toHaveCount(1, { timeout: 35_000 });
    await expect(page.getByRole('heading', { name: 'Published lab diagram' })).toBeVisible();
    await page.getByRole('combobox', { name: 'Diagram', exact: true }).selectOption({ label: 'Automatic network map' });
    await expect(page.getByRole('heading', { name: 'Automatic network map' })).toBeVisible();
    await expect(page.getByText(/queued Automatic topology seed/i)).toBeVisible();
  }
  await page.getByRole('button', { name: 'Sign out' }).click();
  await signIn('addon-viewer');
  await expect(page.getByRole('navigation').getByRole('button', { name: /Network Diagram/ })).toHaveCount(0);
  await page.getByText('Add diagram widget', { exact: true }).click();
  await page.getByLabel('Published diagram').selectOption(String(id));
  await page.getByRole('button', { name: 'Save widget' }).click();
  await expect(page.getByRole('img', { name: 'Network diagram with 2 nodes and 1 links' })).toBeVisible();
  expect((await request.patch(`/api/v1/network-diagram/${id}`, { headers: admin, data: { published: false } })).status()).toBe(200);
  await expect(page.getByText('This diagram is unpublished or unavailable.')).toBeVisible();
  await page.getByRole('button', { name: /Alerts & Events/ }).click();
  await page.getByRole('button', { name: 'errors', exact: true }).click();
  await expect(page.locator('summary').filter({ hasText: /^Probe down$/ })).toBeVisible();
  if (logToken) {
    await page.getByRole('button', { name: 'Infrastructure Logs' }).click();
    await expect(page.locator('summary').filter({ hasText: /^Link down$/ })).toBeVisible();
  }
});
