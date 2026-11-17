import { useMemo, useState } from 'react';
import { createPortal } from 'react-dom';

export type ProbeWindow = { start: string; end: string };
export type ProbeHistory = {
  interval_seconds: number | null;
  latency: { timestamp: string; value: number }[];
  failures: string[];
  successes?: string[];
};

export function plotUptime(window: ProbeWindow, history: ProbeHistory) {
  const start = Date.parse(window.start), end = Date.parse(window.end);
  const inWindow = (time: number) => time >= start && time <= end;
  const latency = history.latency.map(point => ({ time: Date.parse(point.timestamp), value: point.value }))
    .filter(point => inWindow(point.time) && Number.isFinite(point.value) && point.value >= 0)
    .sort((a, b) => a.time - b.time);
  const states = new Map<number, boolean>();
  const successes = history.successes?.map(Date.parse) ?? [];
  for (const time of (history.successes && (successes.length || history.failures.length) ? successes : latency.map(point => point.time))) {
    if (inWindow(time)) states.set(time, true);
  }
  for (const time of history.failures.map(Date.parse)) {
    if (inWindow(time)) states.set(time, false);
  }
  const readings = [...states].sort(([a], [b]) => a - b);
  const gaps = readings.slice(1).map(([time], index) => time - readings[index][0]).sort((a, b) => a - b);
  const interval = history.interval_seconds ? history.interval_seconds * 1000 : Math.min(60_000, gaps[Math.floor(gaps.length / 2)] ?? 1000);
  const segments: { from: number; to: number; up: boolean }[] = [];
  let up = 0, down = 0;
  readings.forEach(([from, available], index) => {
    const next = readings[index + 1]?.[0] ?? end;
    const to = Math.min(end, next - from <= interval * 1.5 ? next : from + interval);
    if (available) up += to - from;
    else down += to - from;
    const previous = segments[segments.length - 1];
    if (previous?.up === available && previous.to === from) previous.to = to;
    else segments.push({ from, to, up: available });
  });
  const outages = segments.filter(segment => !segment.up);
  const available = latency.filter(point => !outages.some(outage => point.time >= outage.from && point.time < outage.to)
    && states.get(point.time) !== false);
  const x = (time: number) => 2 + 120 * (time - start) / Math.max(1, end - start);
  const ceiling = Math.max(1, ...available.map(point => point.value));
  const y = (value: number) => 23 - 19 * value / ceiling;
  const green = available.map((point, i) => {
    const previous = available[i - 1];
    const joined = previous && point.time - previous.time <= interval * 1.5
      && !outages.some(outage => previous.time < outage.to && outage.from < point.time);
    const position = `${x(point.time)},${y(point.value)}`;
    return joined ? `L${position}` : `M${position}l0.01,0`;
  }).join(' ');
  const red = outages.map(({ from, to }) => `M${Math.min(121, x(from))},26 H${Math.max(Math.min(121, x(from)) + 1, x(to))}`).join(' ');
  return { up, down, unknown: Math.max(0, end - start - up - down), green, red, hasData: readings.length > 0 };
}

function duration(milliseconds: number) {
  const seconds = Math.round(milliseconds / 1000);
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export function UptimeSparkline({ window, history, loading, source, label, error, offline }: {
  window?: ProbeWindow; history?: ProbeHistory; loading: boolean; source: string; label: string; error: boolean; offline: boolean;
}) {
  const [anchor, setAnchor] = useState<DOMRect>();
  const chart = useMemo(() => window && history ? plotUptime(window, history) : undefined, [window, history]);
  const summary = chart?.hasData ? `Uptime ${duration(chart.up)} · Downtime ${duration(chart.down)} · No data ${duration(chart.unknown)}`
    : loading ? 'Loading probe history' : error ? 'Probe history unavailable' : 'No ICMP probe history in the last hour';
  return <span className="inline-flex min-w-32 items-center" tabIndex={0} role="img" aria-label={`${label}, ${offline ? 'Currently offline, ' : ''}${summary}`}
    onMouseEnter={event => setAnchor(event.currentTarget.getBoundingClientRect())} onMouseLeave={() => setAnchor(undefined)}
    onFocus={event => setAnchor(event.currentTarget.getBoundingClientRect())} onBlur={() => setAnchor(undefined)}
    onKeyDown={event => { if (event.key === 'Escape') setAnchor(undefined); }}>
    {chart?.hasData || offline ? <svg width="124" height="30" viewBox="0 0 124 30" aria-hidden="true">
      <path d="M2,26 H122" stroke="var(--ops-line)" strokeDasharray="3 3" />
      {chart?.green && <path d={chart.green} stroke="var(--ops-up)" strokeWidth="2" fill="none" strokeLinecap="round" />}
      {chart?.red && <path d={chart.red} stroke="var(--ops-down)" strokeWidth="2.5" fill="none" strokeLinecap="round" />}
      {offline && <path d="M117,26 H122" stroke="var(--ops-down)" strokeWidth="3" strokeLinecap="round" />}
      {!chart?.green && (chart?.up ?? 0) > 0 && <text x="4" y="14" className="fill-current text-[9px] ops-muted">No latency readings</text>}
    </svg> : <span className="text-xs ops-muted">{loading ? 'Loading…' : error ? 'Unavailable' : 'No probe data'}</span>}
    {anchor && createPortal(<div role="tooltip" className="dash-panel pointer-events-none fixed z-50 w-52 p-3 text-xs shadow-lg"
      style={{ left: Math.max(8, Math.min(anchor.left, globalThis.innerWidth - 216)),
        ...(anchor.bottom + 105 < globalThis.innerHeight ? { top: anchor.bottom + 5 } : { bottom: globalThis.innerHeight - anchor.top + 5 }) }}>
      <p className="font-semibold">{source} ICMP · Last hour</p>
      {offline && <p className="mt-1" style={{ color: 'var(--ops-down)' }}>Currently offline</p>}
      {chart?.hasData ? <dl className="mt-2 grid grid-cols-2 gap-1">
        <dt style={{ color: 'var(--ops-up)' }}>Uptime</dt><dd>{duration(chart.up)}</dd>
        <dt style={{ color: 'var(--ops-down)' }}>Downtime</dt><dd>{duration(chart.down)}</dd>
        <dt className="ops-muted">No data</dt><dd>{duration(chart.unknown)}</dd>
      </dl> : <p className="mt-2 ops-muted">{summary}</p>}
      {chart?.hasData && <p className="mt-2 ops-muted">Estimated from probe samples</p>}
    </div>, document.body)}
  </span>;
}
