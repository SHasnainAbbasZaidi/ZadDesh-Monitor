import { expect, test } from '@playwright/test';

test('device rows show reported SNMP hardware and explicit missing identity', async ({ page }) => {
  // Browser-only fixtures; no writes to the running monitoring database.
  await page.addInitScript(() => localStorage.setItem('zaddesh_token', 'browser-fixture'));
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '');
    let json: unknown = [];
    if (path === '/auth/status') json = { setup_required: false };
    else if (path === '/auth/me') json = { id: 1, username: 'hardware-test', role: 'admin', is_active: true };
    else if (path === '/features') json = { incident_workspace: false, network_diagram_beta: false };
    else if (path === '/zabbix/inventory') json = { configured: true, hosts: [
      { hostid: '7', name: 'Reported switch', status: '0', health_status: 'up', local_device_ids: [], interfaces: [],
        vendor: 'Cisco', model: 'Catalyst 3560X-48', identity_source: 'SNMP identity' },
      { hostid: '8', name: 'Unreported host', status: '0', health_status: 'up', local_device_ids: [], interfaces: [] },
    ] };
    else if (!['/devices', '/sites', '/groups'].includes(path)) {
      await route.fulfill({ status: 503, json: { detail: 'Unrelated endpoint unavailable in this focused fixture' } });
      return;
    }
    await route.fulfill({ json });
  });
  await page.goto('/');
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Devices', exact: true }).click();
  const reported = page.getByRole('row').filter({ hasText: 'Reported switch' });
  await expect(reported).toContainText('Cisco');
  await expect(reported).toContainText('Catalyst 3560X-48');
  const missing = page.getByRole('row').filter({ hasText: 'Unreported host' });
  await expect(missing).toContainText('Vendor not reported');
  await expect(missing).toContainText('Model not reported');
});
