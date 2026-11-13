import { expect, it } from 'vitest';
import { linkHealth } from './linkHealth';

it('uses negotiated speed for slow links and gives down status priority', () => {
  const rates = { speed: 1000, expected_speed: 10000 };
  expect(linkHealth({ status: 'up', ...rates }).tone).toBe('warning');
  expect(linkHealth({ status: 'down', ...rates }).tone).toBe('down');
  expect(linkHealth({ status: 'up', speed: 1000, expected_speed: 1000 }).tone).toBe('up');
  expect(linkHealth({ status: 'unknown', ...rates }).tone).toBe('unknown');
});
