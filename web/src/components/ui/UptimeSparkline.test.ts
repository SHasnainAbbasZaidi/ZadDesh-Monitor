import { describe, expect, it } from 'vitest';
import { plotUptime } from './UptimeSparkline';

const time = (second: number) => new Date(Date.UTC(2026, 0, 1) + second * 1000).toISOString();
const window = { start: time(0), end: time(60) };

describe('Uptime sparkline', () => {
  it('draws the failure as a flat red line and excludes stale intervals from uptime', () => {
    const result = plotUptime(window, { interval_seconds: 10,
      latency: [{ timestamp: time(0), value: 4 }, { timestamp: time(10), value: 0 }, { timestamp: time(50), value: 3 }],
      failures: [time(10)] });
    expect([result.up, result.down, result.unknown]).toEqual([20_000, 10_000, 30_000]);
    expect(result.red).toBe('M22,26 H42');
    expect(result.green.match(/M/g)).toHaveLength(2);
  });

  it('counts Zabbix successful pings even when RTT is missing', () => {
    const result = plotUptime(window, { interval_seconds: 10, successes: [time(30)], failures: [time(40), time(50)], latency: [] });
    expect([result.up, result.down, result.unknown]).toEqual([10_000, 20_000, 30_000]);
    expect(result.green).toBe('');
    expect(result.red).toBe('M82,26 H122');
  });
});
