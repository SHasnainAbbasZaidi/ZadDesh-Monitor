import { useEffect, useRef } from 'react';
import { init, use, type EChartsType } from 'echarts/core';
import { GraphChart } from 'echarts/charts';
import { TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { useTheme } from '../../context/ThemeContext';
import { expandedLayout } from './topologyLayout';

use([GraphChart, TooltipComponent, CanvasRenderer]);

export type DiagramPort = { name: string; status: string; speed?: number | null; utilization?: number | null };
export type Diagram = {
  automatic?: boolean;
  id: number; name: string; published: boolean;
  layout: { mode?: string; positions?: Record<string, [number, number]> };
  nodes: { id: number; label: string; status: string; kind: string; alerts: number; site_id?: number; ip?: string; device_id?: number; manual?: boolean; interfaces?: Record<string, DiagramPort> }[];
  links: { id: number; source: number; target: number; source_port: string; target_port: string; method: string; confidence: string; status: string; speed?: number; utilization?: number; manual?: boolean }[];
};
const nodeIcons: Record<string, string> = {
  router: 'diamond', switch: 'rect', firewall: 'triangle', ap: 'pin', server: 'roundRect',
  ups: 'rect', printer: 'roundRect', internet: 'circle', unmanaged: 'circle',
};
const statusColors: Record<string, string> = { up: '#22c55e', down: '#ef4444', warning: '#f59e0b' };

export function positionsFor(diagram: Diagram) {
  if (diagram.layout.mode === 'expanded') return { ...expandedLayout(diagram).positions, ...diagram.layout.positions };
  const levels = new Map<number, number>();
  const adjacency = new Map<number, number[]>();
  for (const link of diagram.links) {
    adjacency.set(link.source, [...adjacency.get(link.source) ?? [], link.target]);
    adjacency.set(link.target, [...adjacency.get(link.target) ?? [], link.source]);
  }
  for (const node of diagram.nodes) {
    if (levels.has(node.id)) continue;
    const queue = [node.id];
    levels.set(node.id, 0);
    for (let index = 0; index < queue.length; index++) {
      for (const neighbor of adjacency.get(queue[index]) ?? []) {
        if (levels.has(neighbor)) continue;
        levels.set(neighbor, (levels.get(queue[index]) ?? 0) + 1);
        queue.push(neighbor);
      }
    }
  }
  const groups = new Map<string, number>();
  const counts = new Map<number, number>();
  const points: Record<string, [number, number]> = {};
  const columns = Math.max(1, Math.ceil(Math.sqrt(diagram.nodes.length * 1.5)));
  for (const [index, node] of diagram.nodes.entries()) {
    const group = diagram.layout.mode === 'site' ? String(node.site_id ?? 'unassigned')
      : diagram.layout.mode === 'subnet' ? node.ip?.split('.').slice(0, 3).join('.') ?? 'unassigned' : undefined;
    if (group !== undefined && !groups.has(group)) groups.set(group, groups.size);
    const level = group !== undefined ? groups.get(group)! : levels.get(node.id) ?? 0;
    const row = counts.get(level) ?? 0;
    counts.set(level, row + 1);
    const position: [number, number] = !diagram.links.length && group === undefined
      ? [(index % columns) * 180, Math.floor(index / columns) * 100]
      : [level * 180 + (row % 2) * 35, row * 92];
    points[node.id] = diagram.layout.positions?.[node.id] ?? position;
  }
  return points;
}

export function svgFor(diagram: Diagram, labels = true) {
  const xml = (text: string) => text.replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;',
  }[character] ?? character));
  const positions = positionsFor(diagram);
  const points = Object.values(positions);
  const minX = Math.min(0, ...points.map(point => point[0])) - 35;
  const minY = Math.min(0, ...points.map(point => point[1])) - 35;
  const width = Math.max(0, ...points.map(point => point[0])) - minX + 160;
  const height = Math.max(0, ...points.map(point => point[1])) - minY + 60;
  const edges = diagram.links.map(link => {
    const a = positions[link.source];
    const b = positions[link.target];
    return a && b ? `<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" stroke="${link.status === 'down' ? '#ef4444' : '#64748b'}" stroke-width="2"${link.status === 'down' ? ' stroke-dasharray="5 4"' : ''}/>` : '';
  });
  const nodes = diagram.nodes.map(node => {
    const [x, y] = positions[node.id];
    return `<circle cx="${x}" cy="${y}" r="10" fill="#64748b" stroke="${statusColors[node.status] ?? '#94a3b8'}" stroke-width="3"/>${labels ? `<text x="${x + 14}" y="${y + 4}" font-family="sans-serif" font-size="12">${xml(node.label)}</text>` : ''}`;
  });
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${minX} ${minY} ${width} ${height}"><title>${xml(diagram.name)}</title>${edges.join('')}${nodes.join('')}</svg>`;
}

type GraphProps = { diagram: Diagram; height?: number; labels?: boolean; draggable?: boolean;
  onSelect?: (kind: 'node' | 'edge', id: number) => void;
  onMove?: (id: number, position: [number, number]) => void;
  selectedNodeId?: number;
  exportGraph?: (exporter: () => string) => void;
};

export default function NetworkGraph({ diagram, height = 460, labels = true, draggable = false, onSelect, onMove, selectedNodeId, exportGraph }: GraphProps) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<EChartsType>();
  const callbacks = useRef({ onSelect, onMove, exportGraph });
  const { theme } = useTheme();
  useEffect(() => { callbacks.current = { onSelect, onMove, exportGraph }; }, [onSelect, onMove, exportGraph]);
  useEffect(() => {
    if (!host.current) return;
    const instance = init(host.current, undefined, { renderer: 'canvas' });
    chart.current = instance;
    const resize = new ResizeObserver(() => instance.resize());
    resize.observe(host.current);
    instance.on('click', (params: { dataType?: string; data?: unknown }) => {
      const item = params.data as { id?: string } | undefined;
      if (item?.id && (params.dataType === 'node' || params.dataType === 'edge')) {
        callbacks.current.onSelect?.(params.dataType, Number(item.id));
      }
    });
    instance.on('mouseup', (params: { dataType?: string; dataIndex?: number; data?: unknown }) => {
      if (!callbacks.current.onMove || params.dataType !== 'node' || params.dataIndex === undefined) return;
      const series = (instance as unknown as { getModel(): { getSeriesByIndex(index: number): {
        getData(): { getItemLayout(index: number): [number, number] }
      } } }).getModel().getSeriesByIndex(0);
      const position = series.getData().getItemLayout(params.dataIndex);
      const item = params.data as { id?: string } | undefined;
      if (item?.id && position?.every(Number.isFinite)) callbacks.current.onMove?.(Number(item.id), position);
    });
    callbacks.current.exportGraph?.(() => instance.getDataURL({
      type: 'png', pixelRatio: 2, backgroundColor: getComputedStyle(document.documentElement).getPropertyValue('--ops-panel').trim(),
    }));
    return () => { resize.disconnect(); instance.dispose(); chart.current = undefined; };
  }, []);

  useEffect(() => {
    const points = positionsFor(diagram);
    const force = diagram.layout.mode === 'force' && !Object.keys(diagram.layout.positions ?? {}).length;
    const themeStyle = getComputedStyle(document.documentElement);
    const text = themeStyle.getPropertyValue('--ops-ink').trim();
    chart.current?.setOption({
      animation: false,
      tooltip: { renderMode: 'richText' },
      series: [{
        id: `diagram-${diagram.id}`, type: 'graph', layout: force ? 'force' : 'none',
        force: { repulsion: 100, edgeLength: 90, layoutAnimation: false },
        roam: true, draggable, scaleLimit: { min: 0.25, max: 5 },
        label: { show: labels, color: text, position: 'bottom', fontSize: 12, width: 145, overflow: 'truncate',
          backgroundColor: themeStyle.getPropertyValue('--ops-panel').trim(), padding: [3, 5], borderRadius: 4 },
        emphasis: { focus: 'adjacency' },
        data: diagram.nodes.map(node => ({
          id: String(node.id), name: node.alerts ? `${node.label} (${node.alerts})` : node.label,
          x: points[node.id][0], y: points[node.id][1], symbol: nodeIcons[node.kind] ?? 'circle', symbolSize: node.alerts ? 36 : 28,
          itemStyle: { color: '#64748b', borderColor: statusColors[node.status] ?? '#94a3b8', borderWidth: 3 },
          value: `${node.kind} · ${node.status}${node.alerts ? ` · ${node.alerts} alerts` : ''}`,
        })),
        links: diagram.links.map(link => ({
          id: String(link.id), source: String(link.source), target: String(link.target),
          value: `${link.source_port} ↔ ${link.target_port} · ${link.method.toUpperCase()} · ${link.status}`,
          label: { show: link.status === 'down', formatter: '×', color: '#ef4444' },
          lineStyle: {
            color: link.status === 'down' ? '#ef4444' : link.utilization == null ? '#64748b'
              : link.utilization < 50 ? '#22c55e' : link.utilization < 80 ? '#f59e0b' : '#ef4444',
            type: link.status === 'down' ? 'dashed' : 'solid',
            width: link.speed ? Math.max(2, Math.min(6, Math.log10(link.speed) - 5)) : 2,
          },
        })),
      }],
    }, { replaceMerge: ['series'] });
  }, [diagram, theme, labels, draggable]);
  useEffect(() => {
    chart.current?.dispatchAction({ type: 'downplay', seriesIndex: 0 });
    if (selectedNodeId !== undefined) {
      const dataIndex = diagram.nodes.findIndex(node => node.id === selectedNodeId);
      if (dataIndex >= 0) chart.current?.dispatchAction({ type: 'highlight', seriesIndex: 0, dataIndex });
    }
  }, [diagram, selectedNodeId]);
  return <div ref={host} style={{ height }} role="img" aria-label={`Network diagram with ${diagram.nodes.length} nodes and ${diagram.links.length} links`} />;
}
