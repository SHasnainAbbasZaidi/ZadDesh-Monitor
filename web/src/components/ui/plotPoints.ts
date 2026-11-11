import type { MetricPoint } from './MetricChart';

export function plotPoints(data: MetricPoint[], intervalSeconds?: number): [number, number | null][] {
  const samples = data.map(({ timestamp, value }): [number, number | null] => [
    Date.parse(/(?:Z|[+-]\d\d:\d\d)$/i.test(timestamp) ? timestamp : timestamp + 'Z'),
    value,
  ]).filter(([time]) => Number.isFinite(time)).sort(([a], [b]) => a - b);
  if (!intervalSeconds) return samples;
  const interval = intervalSeconds * 1000;
  const result: typeof samples = [];
  for (const sample of samples) {
    const previous = result[result.length - 1];
    if (previous && sample[0] - previous[0] > interval * 1.8) result.push([previous[0] + interval, null]);
    result.push(sample);
  }
  return result;
}

