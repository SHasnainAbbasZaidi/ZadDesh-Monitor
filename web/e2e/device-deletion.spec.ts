import { expect, test } from '@playwright/test';
import { randomBytes } from 'node:crypto';

test('select and delete devices without removing unselected rows', async ({ page, request }) => {
  const password = randomBytes(20).toString('hex');
  const setup = await request.post('/api/v1/auth/setup', {
    data: { username: 'deletion-admin', email: 'deletion-admin@example.com', password },
  });
  expect(setup.status()).toBe(201);
  const headers = { Authorization: `Bearer ${(await setup.json()).access_token}` };
  for (const [index, name] of ['Remove this switch', 'Keep this switch'].entries()) {
    expect((await request.post('/api/v1/devices', {
      headers, data: { name, ip: `192.0.2.${index + 1}`, initial_check_types: ['icmp'] },
    })).status()).toBe(201);
  }

  await page.goto('/');
  await page.getByLabel('Username').fill('deletion-admin');
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Sign In', exact: true }).click();
  await page.getByRole('button', { name: 'Devices', exact: true }).click();
  const all = page.getByRole('checkbox', { name: 'Select all visible devices' });
  const keep = page.getByRole('checkbox', { name: 'Select Keep this switch' });
  await all.check();
  await expect(page.getByText('2 selected')).toBeVisible();
  await keep.uncheck();
  await page.getByRole('button', { name: 'Delete selected', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Delete selected devices' });
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(page.getByRole('checkbox', { name: 'Select Remove this switch' })).toBeChecked();
  await page.getByRole('button', { name: 'Delete selected', exact: true }).click();
  await dialog.getByRole('button', { name: 'Confirm deletion' }).click();
  await expect(page.getByRole('row', { name: /Remove this switch/ })).toHaveCount(0);
  await expect(page.getByRole('row', { name: /Keep this switch/ })).toBeVisible();
});
