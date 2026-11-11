import { lazy, Suspense } from 'react';

export interface MetricChartProps {
  title: string;
  unit: string;
  data?: MetricPoint[];
  channels?: MetricChannel[];
  range?: { start: number; end: number };
  color?: string;
  height?: number;
  intervalSeconds?: number;
}

export type MetricPoint = { timestamp: string; value: number | null };
export interface MetricChannel {
  id: number;
  name: string;
  color: string;
  data: MetricPoint[];
  failures: MetricPoint[];
  intervalSeconds?: number;
}

const Plot = lazy(() => import('./MetricPlot'));
export function MetricChart(props: MetricChartProps) {
  const height = props.height ?? 220;
  if (!props.data?.length && !props.channels?.some(channel => channel.data.length || channel.failures.length)) {
    return <div style={{ minHeight: height }} className="ops-empty flex items-center justify-center">No samples in this range. Check that a collector is polling this device.</div>;
  }
  return <Suspense fallback={<div style={{ minHeight: height }} className="ops-empty" role="status">Loading chart…</div>}><Plot {...props} height={height} /></Suspense>;
}
