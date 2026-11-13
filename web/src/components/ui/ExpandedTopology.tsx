import { useMemo, useRef, useState } from 'react';
import { Cloud, HardDrive, Monitor, Network, Printer, Router, Server, Shield, Wifi, Zap } from 'lucide-react';
import type { Diagram } from './NetworkGraph';
import { deviceHeight, deviceWidth, expandedLayout } from './topologyLayout';

function equipment(node: Diagram['nodes'][number]) {
  const label = node.label.toLowerCase();
  if (node.kind === 'switch' || /\b(?:switch|dist)\b|[_-]dis(?:t)?[_0-9]/.test(label)) return Network;
  if (node.kind === 'server' || label.includes('server')) return Server;
  if (node.kind === 'router' || label.includes('router')) return Router;
  if (node.kind === 'firewall') return Shield;
  if (node.kind === 'ap') return Wifi;
  if (node.kind === 'internet') return Cloud;
  if (node.kind === 'printer') return Printer;
  if (node.kind === 'ups') return Zap;
  return node.device_id ? Monitor : HardDrive;
}

export function ExpandedTopology({ diagram, selected, locked, positions, onSelect, onMove, svgRef }: {
  diagram: Diagram;
  selected?: { kind: 'node' | 'edge'; id: number };
  locked: boolean;
  positions: Record<string, [number, number]>;
  onSelect: (kind: 'node' | 'edge', id: number) => void;
  onMove: (id: number, position: [number, number]) => void;
  svgRef: React.RefObject<SVGSVGElement>;
}) {
  const layout = useMemo(() => expandedLayout(diagram), [diagram]);
  const [zoom, setZoom] = useState(1);
  const drag = useRef<{ id: number; x: number; y: number; left: number; top: number }>();
  const points: Record<string, [number, number]> = {
    ...layout.positions, ...(diagram.layout.mode === 'expanded' ? diagram.layout.positions : {}), ...positions,
  };
  const width = Math.max(layout.width, ...Object.values(points).map(([x]) => x + deviceWidth + 30));
  const height = Math.max(layout.height, ...Object.values(points).map(([, y]) => y + deviceHeight + 30));
  return <div className="expanded-topology">
    <div className="expanded-topology-tools" aria-label="Diagram zoom">
      <button className="ops-button" aria-label="Zoom out" disabled={zoom <= .5} onClick={() => setZoom(value => Math.max(.5, value - .25))}>−</button>
      <button className="ops-button" onClick={() => setZoom(1)} aria-label="Actual size">{Math.round(zoom * 100)}%</button>
      <button className="ops-button" aria-label="Zoom in" disabled={zoom >= 2} onClick={() => setZoom(value => Math.min(2, value + .25))}>+</button>
    </div>
    <div className="expanded-topology-scroll" tabIndex={0} role="region" aria-label="Scrollable topology canvas">
      <svg ref={svgRef} className="expanded-topology-map" width={width * zoom} height={height * zoom} viewBox={`0 0 ${width} ${height}`}
        role="img" aria-label={`Network diagram with ${diagram.nodes.length} nodes and ${diagram.links.length} links`}>
        <rect width={width} height={height} fill="var(--ops-bg)" />
        <defs><pattern id="topology-grid" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M20 0H0V20" stroke="var(--ops-line)" strokeOpacity=".65" fill="none" /></pattern></defs>
        <rect width={width} height={height} fill="url(#topology-grid)" />
        {layout.zones.map(zone => <g key={zone.name}>
          <rect {...{ x: zone.x, y: zone.y, width: zone.width, height: zone.height }} rx="14" fill="var(--ops-subtle)" stroke="var(--ops-line)" />
          <text x={zone.x + 28} y={zone.y + 33} fill="var(--ops-accent)" fontSize="14" fontWeight="650">{zone.name}</text>
        </g>)}
        {diagram.links.map(link => {
          const a = points[link.source], b = points[link.target];
          if (!a || !b || link.source === link.target) return null;
          const [left, right] = a[0] <= b[0] ? [a, b] : [b, a];
          const vertical = right[0] - left[0] < deviceWidth;
          const start = vertical && left[1] > right[1] ? right : left;
          const end = start === left ? right : left;
          const x1 = start[0] + (vertical ? deviceWidth / 2 : deviceWidth), y1 = start[1] + (vertical ? deviceHeight : deviceHeight / 2);
          const x2 = end[0] + (vertical ? deviceWidth / 2 : 0), y2 = end[1] + (vertical ? 0 : deviceHeight / 2);
          const mid = vertical ? (y1 + y2) / 2 : (x1 + x2) / 2;
          const path = vertical ? `M${x1} ${y1} C${x1} ${mid} ${x2} ${mid} ${x2} ${y2}` : `M${x1} ${y1} C${mid} ${y1} ${mid} ${y2} ${x2} ${y2}`;
          const active = selected?.kind === 'edge' && selected.id === link.id;
          const connected = selected?.kind === 'node' && (selected.id === link.source || selected.id === link.target);
          const uplink = layout.groupByNode.get(link.source) !== layout.groupByNode.get(link.target) && link.speed === 10_000_000_000;
          const offset = uplink ? ((link.id % 7) - 3) * 7 : 0;
          const cable = uplink && vertical ? `M${x1 + offset} ${y1} C${x1 + offset} ${mid} ${x2 + offset} ${mid} ${x2 + offset} ${y2}` : path;
          const color = link.status === 'down' ? 'var(--ops-down)' : link.utilization != null && link.utilization >= 80 ? 'var(--ops-warning)' : 'var(--ops-accent)';
          return <g key={link.id} onClick={() => onSelect('edge', link.id)} className="cursor-pointer">
            <title>{link.source_port} ↔ {link.target_port} · {link.status}</title>
            <path d={cable} fill="none" stroke={color} opacity={active || connected || uplink ? 1 : .48} strokeWidth={active || connected || uplink ? 3 : 1.6} strokeDasharray={link.status === 'down' ? '6 4' : undefined} />
            <path d={cable} fill="none" stroke="transparent" strokeWidth={uplink ? '7' : '14'} />
          </g>;
        })}
        {diagram.nodes.map(node => {
          const [x, y] = points[node.id];
          const Icon = equipment(node);
          const active = selected?.kind === 'node' && selected.id === node.id;
          const color = ({ up: 'var(--ops-up)', down: 'var(--ops-down)', warning: 'var(--ops-warning)' })[node.status] ?? 'var(--ops-muted)';
          return <g key={node.id} transform={`translate(${x} ${y})`} className={locked ? 'cursor-pointer' : 'topology-movable'} onClick={() => onSelect('node', node.id)}
            onPointerDown={event => {
              if (locked || event.button !== 0) return;
              drag.current = { id: node.id, x: event.clientX, y: event.clientY, left: x, top: y };
              event.currentTarget.setPointerCapture(event.pointerId);
            }} onPointerMove={event => {
              const start = drag.current;
              if (start?.id === node.id) onMove(node.id, [Math.max(0, start.left + (event.clientX - start.x) / zoom), Math.max(0, start.top + (event.clientY - start.y) / zoom)]);
            }} onLostPointerCapture={() => { drag.current = undefined; }}>
            <title>{node.label} · {node.status}{node.ip ? ` · ${node.ip}` : ''}</title>
            <rect width={deviceWidth} height={deviceHeight} rx="8" fill="var(--ops-panel)" stroke={active ? 'var(--ops-accent)' : 'var(--ops-line)'} strokeWidth={active ? 2 : 1} />
            <Icon x={13} y={12} width={26} height={26} color="var(--ops-accent)" aria-hidden="true" />
            <circle cx={deviceWidth - 14} cy={16} r="4" fill={color} />
            <svg x="13" y="42" width={deviceWidth - 26} height="18" overflow="hidden">
              <text y="13" fill="var(--ops-ink)" fontSize="12" fontWeight="650">{node.label}</text>
            </svg>
            <text x="13" y="73" fill="var(--ops-muted)" fontSize="10">{node.ip || node.status}</text>
          </g>;
        })}
      </svg>
    </div>
  </div>;
}
