import { useEffect, useRef } from 'react';
import { connect, init, use } from 'echarts/core';
import type { EChartsType } from 'echarts/core';
import { LineChart, ScatterChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, DataZoomComponent, AxisPointerComponent, AriaComponent, MarkLineComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { useTheme } from '../../context/ThemeContext';
import type { MetricChartProps } from './MetricChart';
import { plotPoints } from './plotPoints';

use([LineChart, ScatterChart, GridComponent, TooltipComponent, DataZoomComponent, AxisPointerComponent, AriaComponent, MarkLineComponent, CanvasRenderer]);

function csvName(value: string) {
  return '"' + (/^[\s=+@-]/.test(value) ? "'" : '') + value.replace(/"/g, '""') + '"';
}

export default function MetricPlot({ title, unit, data, channels, range, failureBucketSeconds, color, height, intervalSeconds, annotations }: MetricChartProps) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<EChartsType | null>(null);
  const { theme } = useTheme();
  useEffect(() => {
    if (!host.current) return;
    const el = host.current;
    const instance = init(el, undefined, { renderer: 'canvas' });
    chart.current = instance;
    instance.group = 'device-metrics';
    connect('device-metrics');
    const resize = new ResizeObserver(() => instance.resize());
    resize.observe(el);
    return () => { resize.disconnect(); instance.dispose(); chart.current = null; };
  }, []);

  useEffect(() => {
    const ink = getComputedStyle(document.documentElement).getPropertyValue('--ops-muted').trim();
    const line = getComputedStyle(document.documentElement).getPropertyValue('--ops-line').trim();
    const axisLabel = { color: ink };
    const markers = annotations?.map(item => ({ xAxis: Date.parse(item.timestamp), name: item.label }));
    const times = markers?.map(marker => marker.xAxis) ?? [];
    const sampleTimes = markers ? plotPoints(data ?? []).map(([time]) => time) : [];
    const xAxis = { type: 'time', min: range?.start ?? (times.length ? Math.min(...times, ...sampleTimes) : undefined),
      max: range?.end ?? (times.length ? Math.max(...times, ...sampleTimes) : undefined), axisLabel, axisLine: { lineStyle: { color: line } } };
    const outages = channels?.filter(channel => channel.failures.length) ?? [];
    const hasOutages = outages.length > 0;
    const outageHeight = 16;
    const failureTimes = new Map<number, number>();
    const bucketMs = Math.max(1, failureBucketSeconds ?? 0) * 1000;
    for (const plot of outages) {
      const buckets = new Set(plotPoints(plot.failures).map(([time]) => Math.floor(time / bucketMs) * bucketMs));
      for (const time of buckets) failureTimes.set(time, (failureTimes.get(time) ?? 0) + 1);
    }
    const failureColor = getComputedStyle(document.documentElement).getPropertyValue('--ops-down').trim();
    const plots = channels ?? [{ id: 0, name: title, data: data ?? [], color, intervalSeconds }];
    chart.current?.setOption({
      animation: false,
      color: [color || getComputedStyle(document.documentElement).getPropertyValue('--ops-accent').trim()],
      aria: { enabled: true, label: { description: `${title}, measured in ${unit} over time.${hasOutages ? ' Failed probes are shown below the latency axis.' : ''}` } },
      grid: hasOutages ? [
        { left: 54, right: 18, top: 18, bottom: 39 + outageHeight },
        { left: 54, right: 18, height: outageHeight, bottom: 31 },
      ] : [{ left: 54, right: 18, top: 18, bottom: 32 }],
      tooltip: { trigger: 'axis', renderMode: 'richText', valueFormatter: (value: unknown) => value == null ? 'No sample' : `${Number(value).toLocaleString()} ${unit}` },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      xAxis: hasOutages ? [{ ...xAxis, axisLabel: { show: false } }, { ...xAxis, gridIndex: 1 }] : [xAxis],
      yAxis: [
        { type: 'value', axisLabel, splitLine: { lineStyle: { color: line } }, scale: true },
        ...(hasOutages ? [{ type: 'value', gridIndex: 1, min: 0, max: 2, show: false }] : []),
      ],
      dataZoom: [{ type: 'inside', xAxisIndex: hasOutages ? [0, 1] : [0], zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false }],
      series: [
        ...plots.map(plot => ({
          id: `latency-${plot.id}`, name: plot.name, type: 'line', data: plotPoints(plot.data, plot.intervalSeconds),
          itemStyle: { color: plot.color }, lineStyle: { color: plot.color }, connectNulls: false,
          showSymbol: plot.data.length < 12, sampling: 'lttb',
          ...(channels ? {} : { areaStyle: { opacity: 0.08 } }),
          ...(markers ? { markLine: { symbol: 'none', label: { formatter: '{b}', color: ink }, data: markers } } : {}),
        })),
        ...(hasOutages ? [{
          id: 'probe-failures', name: 'Failed probes', type: 'scatter', xAxisIndex: 1, yAxisIndex: 1,
          data: Array.from(failureTimes, ([time, count]) => ({
            name: `${new Date(Math.max(time, range?.start ?? time)).toLocaleString()} · ${count} ${count === 1 ? 'device' : 'devices'} with failed probes`,
            value: [Math.max(time, range?.start ?? time), 1],
          })),
          itemStyle: { color: failureColor, opacity: .85 }, symbol: 'rect', symbolSize: [2, 10],
          tooltip: { trigger: 'item', formatter: '{b}' },
        }] : []),
      ],
    }, { replaceMerge: ['series', 'grid', 'xAxis', 'yAxis'] });
  }, [title, unit, data, channels, range, failureBucketSeconds, color, theme, intervalSeconds, annotations]);

  const download = (url: string, suffix: string) => {
    const link = document.createElement('a');
    link.href = url;
    link.download = `${title.toLowerCase().replace(/[^a-z0-9]+/g, '-') }.${suffix}`;
    link.click();
  };
  return <div>
    <div className="flex flex-wrap justify-between items-center gap-2">
      <h3 className="font-semibold text-sm">{title} <span className="ops-muted">· {unit}</span></h3>
      <div className="flex gap-2">
        <button className="ops-button" aria-label={`Export ${title} CSV`} onClick={() => {
          const csv = channels
            ? ['timestamp,device,latency_ms,status', ...channels.flatMap(channel => [
              ...plotPoints(channel.data).filter(([, value]) => value !== null).map(([time, value]) => `${new Date(time).toISOString()},${csvName(channel.name)},${value},`),
              ...plotPoints(channel.failures).map(([time]) => `${new Date(time).toISOString()},${csvName(channel.name)},,probe failed`),
            ])].join('\n')
            : ['timestamp,value', ...plotPoints(data ?? []).map(([time, value]) => `${new Date(time).toISOString()},${value ?? ''}`)].join('\n');
          const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
          download(url, 'csv');
          window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        }}>CSV</button>
        <button className="ops-button" aria-label={`Export ${title} PNG`} onClick={() => {
          const url = chart.current?.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: getComputedStyle(document.documentElement).getPropertyValue('--ops-panel').trim() });
          if (url) download(url, 'png');
        }}>PNG</button>
      </div>
    </div>
    <div ref={host} style={{ height }} />
  </div>;
}
