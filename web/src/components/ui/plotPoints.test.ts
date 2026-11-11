import { describe, expect, it } from 'vitest';
import { plotPoints } from './plotPoints';

describe('chart time axis', () => {
  it('treats SQLite timestamps as UTC and inserts a gap for missed polls', () => {
    const points = plotPoints([
      { timestamp: '2025-01-01T00:03:00+00:00', value: 8 },
      { timestamp: '2025-01-01T00:00:00', value: 5 },
    ], 60);
    expect(points).toEqual([
      [Date.parse('2025-01-01T00:00:00Z'), 5],
      [Date.parse('2025-01-01T00:01:00Z'), null],
      [Date.parse('2025-01-01T00:03:00Z'), 8],
    ]);
  });
});
