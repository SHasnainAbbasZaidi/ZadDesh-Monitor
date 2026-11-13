import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Cable, Focus, Lock, LockOpen, Network, Search, Server } from 'lucide-react';
import { Card } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import type { Diagram } from '../components/ui/NetworkGraph';
import { ExpandedTopology } from '../components/ui/ExpandedTopology';
import { api, request } from '../services/api';
import { useAddonUpdates } from '../services/addonUpdates';
import type { Check, Site } from '../types';

const Graph = lazy(() => import('../components/ui/NetworkGraph'));
const layouts = [['hierarchical', 'Hierarchical'], ['force', 'Force-directed'], ['site', 'By site'], ['subnet', 'By subnet']];
const kinds = ['router', 'switch', 'firewall', 'ap', 'server', 'ups', 'printer', 'unmanaged', 'internet'];
type DiagramItem = Pick<Diagram, 'id' | 'name' | 'published' | 'automatic'>;
type Run = { id: number; status: string; device: string; message?: string };
type Change = { id: number; description: string; decision: string };

function download(name: string, value: Blob | string) {
  const link = document.createElement('a');
  link.href = typeof value === 'string' ? value : URL.createObjectURL(value);
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  if (typeof value !== 'string') window.setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

export default function NetworkDiagram() {
  const [diagrams, setDiagrams] = useState<DiagramItem[]>([]);
  const [id, setId] = useState<number>();
  const [name, setName] = useState('');
  const [graph, setGraph] = useState<Diagram>();
  const [checks, setChecks] = useState<Check[]>([]);
  const [seeds, setSeeds] = useState<number[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [changes, setChanges] = useState<Change[]>([]);
  const [selection, setSelection] = useState<{ kind: 'node' | 'edge'; id: number }>();
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [search, setSearch] = useState('');
  const [locked, setLocked] = useState(true);
  const [positions, setPositions] = useState<Record<string, [number, number]>>({});
  const [fit, setFit] = useState(0);
  const [expanded, setExpanded] = useState(true);
  const mapSvg = useRef<SVGSVGElement>(null);
  const inspector = useRef<HTMLElement>(null);
  const choseDiagram = useRef(false);
  const png = useRef<() => string>();
  const endpoint = `/network-diagram/${id}`;
  const selectedNode = graph?.nodes.find(node => selection?.kind === 'node' && node.id === selection.id);
  const selectedLink = graph?.links.find(link => selection?.kind === 'edge' && link.id === selection.id);
  useAddonUpdates(() => setRefresh(value => value + 1));

  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([request<DiagramItem[]>('/network-diagram', { signal: controller.signal }),
      api.getChecks({ check_type: 'snmp', is_enabled: true })])
      .then(([items, candidates]) => {
        if (controller.signal.aborted) return;
        setDiagrams(items);
        setId(current => choseDiagram.current ? current : items[0]?.id);
        setChecks(candidates.filter(check => check.params.version === '3' && !check.params.credential));
      }).catch(failure => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [refresh]);

  useEffect(() => {
    if (!id) return;
    const controller = new AbortController();
    void Promise.all([
      request<Diagram>(endpoint, { signal: controller.signal }),
      request<Run[]>(endpoint + '/discovery', { signal: controller.signal }),
      request<Change[]>(endpoint + '/changes', { signal: controller.signal }),
    ]).then(([diagram, runItems, changeItems]) => {
      if (!controller.signal.aborted) { setGraph(diagram); setRuns(runItems); setChanges(changeItems); setError(''); }
    }).catch(failure => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [endpoint, id, refresh]);

  async function mutate(path: string, method: string, body?: object) {
    try {
      setError('');
      await request(path, { method, body: body ? JSON.stringify(body) : undefined });
      setRefresh(value => value + 1);
      return true;
    } catch (failure) { setError((failure as Error).message); }
    return false;
  }

  const activeGraph = graph?.id === id ? graph : undefined;
  const visible = activeGraph && search.trim() ? activeGraph.nodes.filter(node => node.label.toLowerCase().includes(search.trim().toLowerCase())) : activeGraph?.nodes;
  const nodesById = new Map(activeGraph?.nodes.map(node => [node.id, node]));
  const connections = selectedNode ? activeGraph?.links.flatMap(link => {
    if (link.source === selectedNode.id) return [{ link, local: link.source_port, remote: link.target_port, peer: nodesById.get(link.target) }];
    if (link.target === selectedNode.id) return [{ link, local: link.target_port, remote: link.source_port, peer: nodesById.get(link.source) }];
    return [];
  }) ?? [] : [];
  const ports = Object.values(selectedNode?.interfaces ?? {}).sort((left, right) => left.name.localeCompare(right.name, undefined, { numeric: true }));

  function inspect(kind: 'node' | 'edge', item: number) {
    setSelection({ kind, id: item });
  }
  useEffect(() => {
    if (selection) inspector.current?.scrollIntoView({ block: 'start' });
  }, [selection]);
  // Keep the graph stable while the operator is moving nodes. Refresh data once the layout is locked.
  const editingGraph = useRef<Diagram>();
  useEffect(() => { if (locked) editingGraph.current = undefined; }, [locked]);
  const canvasGraph = !locked && editingGraph.current?.id === id ? editingGraph.current : activeGraph;

  function exportExpanded(format: 'svg' | 'png') {
    if (!mapSvg.current) return;
    const svg = mapSvg.current.cloneNode(true) as SVGSVGElement;
    svg.setAttribute('width', String(mapSvg.current.viewBox.baseVal.width));
    svg.setAttribute('height', String(mapSvg.current.viewBox.baseVal.height));
    const theme = getComputedStyle(mapSvg.current);
    const markup = new XMLSerializer().serializeToString(svg).replace(/var\((--ops-[\w-]+)\)/g, (_, name) => theme.getPropertyValue(name).trim());
    const blob = new Blob([markup], { type: 'image/svg+xml' });
    if (format === 'svg') { download('network-diagram.svg', blob); return; }
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      const canvas = document.createElement('canvas');
      canvas.width = img.width; canvas.height = img.height;
      canvas.getContext('2d')?.drawImage(img, 0, 0);
      canvas.toBlob(value => { if (value) download('network-diagram.png', value); });
    };
    img.onerror = () => { URL.revokeObjectURL(url); setError('Could not export the diagram as PNG.'); };
    img.src = url;
  }

  return <section className="space-y-4">
    <h1 className="ops-heading">Network Diagram</h1>
    {error && <p className="ops-notice" role="alert">{error}</p>}
    <Card><div className="flex flex-wrap items-end gap-3">
      <label className="text-sm">Diagram<select className="dash-select block" value={id ?? ''} onChange={event => {
        choseDiagram.current = true;
        setId(Number(event.target.value)); setPositions({}); setSelection(undefined); setSearch(''); setLocked(true);
      }}><option value="" disabled>Choose a diagram</option>{diagrams.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <form className="flex items-end gap-2" onSubmit={async event => {
        event.preventDefault();
        try {
          const item = await request<DiagramItem>('/network-diagram', { method: 'POST', body: JSON.stringify({ name }) });
          choseDiagram.current = true;
          setDiagrams(previous => [...previous, item]); setId(item.id); setName(''); setPositions({}); setSelection(undefined);
        } catch (failure) { setError((failure as Error).message); }
      }}>
        <label className="text-sm">New diagram name<input className="dash-select block" value={name} onChange={e => setName(e.target.value)} maxLength={100} required /></label>
        <button className="ops-button" type="submit">Create diagram</button>
      </form>
    </div></Card>
    {!diagrams.some(item => item.automatic) && <p className="ops-muted text-sm">
      An automatic map appears after ZadDesh finds an enabled device check with a stored SNMPv3 credential and a permitted IP.
    </p>}
    {id && <details className="dash-panel p-3"><summary className="cursor-pointer font-semibold text-sm">SNMPv3 discovery</summary>
      <div className="pt-3">
      {activeGraph?.automatic && <p className="mb-3 text-sm ops-muted">Saved SNMPv3 checks are queued automatically. Links appear when devices report LLDP or CDP neighbors.</p>}
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm">SNMPv3 seed checks<select className="dash-select block" multiple size={Math.min(5, Math.max(2, checks.length))}
          value={seeds.map(String)} onChange={event => setSeeds(Array.from(event.target.selectedOptions, option => Number(option.value)))}>
          {checks.map(check => <option key={check.id} value={check.id}>{check.name} · {check.target}</option>)}
        </select></label>
        <button className="ops-button" disabled={!seeds.length} onClick={() => void mutate(endpoint + '/discovery', 'POST', { checks: seeds })}>Run discovery now</button>
      </div>
      {!checks.length && <p className="ops-muted text-sm">Provision an enabled device check with stored SNMPv3 credentials first.</p>}
      {runs.map(run => <p key={run.id} className="mt-2 text-sm"><Badge status={run.status} /> {run.device}{run.message ? ` · ${run.message}` : ''}</p>)}
      </div>
    </details>}
    {activeGraph && <section className="dash-panel" aria-label="Topology workspace">
      <div className="topology-toolbar">
        <div className="min-w-0 mr-auto"><h2 className="flex items-center gap-2 font-semibold"><Network size={17} aria-hidden="true" />{activeGraph.name}</h2>
          <p className="ops-muted text-xs mt-1">{activeGraph.nodes.length} devices · {activeGraph.links.length} links</p></div>
        <label className="text-xs">Layout<select className="dash-select block" disabled={!locked} value={expanded ? 'expanded' : activeGraph.layout.mode ?? 'hierarchical'} onChange={async e => {
          const mode = e.target.value;
          if (mode === 'expanded') setExpanded(true);
          else if (mode === activeGraph.layout.mode || await mutate(endpoint, 'PATCH', { layout: mode, positions: {} })) setExpanded(false);
        }}><option value="expanded">Expanded device map</option>{layouts.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <button className="ops-button" onClick={() => {
          if (locked) editingGraph.current = activeGraph;
          if (!locked && Object.keys(positions).length && !window.confirm('Discard unsaved node positions?')) return;
          if (!locked) setPositions({});
          setLocked(!locked);
        }}>{locked ? <Lock size={14} /> : <LockOpen size={14} />}{locked ? 'Unlock layout' : 'Lock layout'}</button>
        <button className="ops-button" disabled={!Object.keys(positions).length}
          onClick={async () => {
            const layout = expanded ? 'expanded' : activeGraph.layout.mode ?? 'hierarchical';
            const saved = activeGraph.layout.mode === layout ? activeGraph.layout.positions : {};
            if (await mutate(endpoint, 'PATCH', { layout, positions: { ...saved, ...positions } })) {
              setPositions({}); setLocked(true);
            }
          }}>Save layout</button>
        {!expanded && <button className="ops-button" onClick={() => setFit(value => value + 1)}><Focus size={14} />Fit to screen</button>}
        <button className="ops-button" onClick={() => void mutate(endpoint, 'PATCH', { published: !activeGraph.published })}>{activeGraph.published ? 'Unpublish' : 'Publish'}</button>
        <button className="ops-button" onClick={() => download('network-diagram.json', new Blob([JSON.stringify(activeGraph, null, 2)], { type: 'application/json' }))}>JSON</button>
        <button className="ops-button" disabled={!activeGraph.nodes.length} onClick={async () => {
          if (expanded) { exportExpanded('svg'); return; }
          const { svgFor } = await import('../components/ui/NetworkGraph');
          download('network-diagram.svg', new Blob([svgFor(activeGraph)], { type: 'image/svg+xml' }));
        }}>SVG</button>
        <button className="ops-button" disabled={!activeGraph.nodes.length} onClick={() => {
          if (expanded) exportExpanded('png');
          else if (png.current) download('network-diagram.png', png.current());
        }}>PNG</button>
      </div>
      <div className={`topology-workbench${expanded ? ' topology-workbench-expanded' : ''}`}>
        <aside className="topology-devices" aria-label="Diagram devices">
          <label className="block text-sm font-semibold" htmlFor="topology-find">Devices</label>
          <div className="relative mt-2"><Search size={14} className="absolute left-2 top-2.5 ops-muted" aria-hidden="true" />
            <input id="topology-find" className="dash-select w-full pl-7" placeholder="Find a node" value={search} onChange={event => setSearch(event.target.value)} />
          </div>
          <ul className="topology-device-list">
            {visible?.map(node => <li key={node.id}><button type="button" aria-pressed={selectedNode?.id === node.id} onClick={() => inspect('node', node.id)}>
              <Server size={15} aria-hidden="true" /><span><strong>{node.label}</strong><small>{node.kind} · {node.status}</small></span></button></li>)}
          </ul>
          {!visible?.length && <p className="ops-muted text-xs mt-3">{search ? 'No matching devices' : 'No devices on this diagram'}</p>}
        </aside>
        <div className="topology-canvas">
      {activeGraph.nodes.length && expanded ? <ExpandedTopology key={id} diagram={canvasGraph ?? activeGraph} selected={selection}
        locked={locked} positions={positions} svgRef={mapSvg} onSelect={inspect}
        onMove={(node, point) => setPositions(previous => ({ ...previous, [node]: point }))} />
        : activeGraph.nodes.length ? <Suspense fallback={<p role="status">Loading diagram…</p>}>
        <Graph key={`${id}:${fit}`} diagram={canvasGraph ?? activeGraph} draggable={!locked} onSelect={inspect}
          onMove={!locked ? (node, point) => setPositions(previous => ({ ...previous, [node]: point })) : undefined}
          selectedNodeId={selectedNode?.id} height={610} exportGraph={exporter => { png.current = exporter; }} />
      </Suspense> : <p className="ops-empty">{activeGraph.automatic
        ? 'Waiting for successful SNMPv3 discovery results.'
        : 'Run discovery or add a node to populate this diagram.'}</p>}
        </div>
        <aside ref={inspector} className="topology-inspector" aria-label="Topology inspector">
          <h3 className="flex items-center gap-2 font-semibold text-sm"><Cable size={16} aria-hidden="true" />Inspector</h3>
      {selectedNode && <div className="mt-4 space-y-2" aria-label="Node details">
        <h3 className="font-semibold">{selectedNode.label} · {selectedNode.kind}</h3>
        <p><Badge status={selectedNode.status} /> {selectedNode.ip} · {selectedNode.alerts} active alerts</p>
        <section aria-label="Connected ports" className="space-y-2">
          <h4 className="text-sm font-semibold">Connected ports ({connections.length})</h4>
          {!connections.length && <p className="text-xs ops-muted">No links to named devices have been discovered or added.</p>}
          <ul className="space-y-1">{connections.map(({ link, local, remote, peer }) => <li key={link.id}>
            <button className="ops-button whitespace-normal text-left w-full" onClick={() => inspect('edge', link.id)}>
              <span className="min-w-0 break-words">{local || 'Unknown port'} ↔ {peer?.label} · {remote || 'Unknown port'}</span>
              <Badge status={link.status} />
            </button>
          </li>)}</ul>
        </section>
        <details className="entry-details"><summary className="text-sm font-semibold">Discovered interfaces ({ports.length})</summary>
          {ports.length ? <ul className="divide-y divide-slate-500/25">{ports.map((port, index) => <li key={`${port.name}-${index}`} className="flex flex-wrap items-center gap-2 py-1 text-xs">
            <strong className="break-all">{port.name}</strong><Badge status={port.status} />
            {port.speed != null && <span className="ops-muted">{(port.speed / 1_000_000).toLocaleString()} Mbps</span>}
            {port.utilization != null && <span className="ops-muted">{port.utilization.toFixed(1)}% utilized</span>}
          </li>)}</ul> : <p className="text-xs ops-muted">No SNMP interface data has been collected for this device.</p>}
        </details>
      </div>}
      {selectedLink && <div className="mt-4 space-y-2" aria-label="Link details">
        <h3 className="font-semibold">{nodesById.get(selectedLink.source)?.label} · {selectedLink.source_port || 'Unknown port'} ↔ {nodesById.get(selectedLink.target)?.label} · {selectedLink.target_port || 'Unknown port'}</h3>
        <p><Badge status={selectedLink.status} /> {selectedLink.method.toUpperCase()} · {selectedLink.confidence} confidence
          {selectedLink.speed != null && ` · ${selectedLink.speed.toLocaleString()} bps`}
          {selectedLink.utilization != null && ` · ${selectedLink.utilization.toFixed(1)}% utilization`}</p>
        <button className="ops-button" onClick={() => void mutate(`${endpoint}/links/${selectedLink.id}`, 'DELETE')}>Remove link</button>
      </div>}
      <details key={selectedNode?.id ?? (selectedLink ? `link-${selectedLink.id}` : 'new')} className="mt-4" open={Boolean(selectedNode || selectedLink)}><summary className="cursor-pointer text-sm font-semibold">{selectedNode ? 'Configure device on map' : selectedLink ? 'Configure link on map' : 'Add a device or link'}</summary>
        <form className="my-2 flex flex-wrap items-end gap-2" key={`node-${selectedNode?.id ?? 'new'}`} onSubmit={event => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          void mutate(`${endpoint}/nodes/${selectedNode?.id ?? 0}`, 'PUT', { label: data.get('label'), kind: data.get('kind') });
        }}>
          <label className="text-sm">Node label<input className="dash-select block" name="label" defaultValue={selectedNode?.label} required maxLength={255} /></label>
          <label className="text-sm">Node type<select className="dash-select block" name="kind" defaultValue={selectedNode?.kind ?? 'unmanaged'}>
            {kinds.map(kind => <option key={kind}>{kind}</option>)}</select></label>
          <button type="submit" className="ops-button">{selectedNode ? 'Save node' : 'Add node'}</button>
          {selectedNode && <button type="button" className="ops-button" onClick={() => void mutate(`${endpoint}/nodes/${selectedNode.id}`, 'DELETE')}>Remove node</button>}
        </form>
        {activeGraph.nodes.length > 1 && <form className="flex flex-wrap items-end gap-2" key={`link-${selectedLink?.id ?? 'new'}`} onSubmit={event => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          void mutate(`${endpoint}/links/${selectedLink?.id ?? 0}`, 'PUT', {
            source: Number(data.get('source')), target: Number(data.get('target')),
            source_port: data.get('source_port'), target_port: data.get('target_port'),
          });
        }}>
          {(['source', 'target'] as const).map(name => <label key={name} className="text-sm capitalize">{name}<select name={name} className="dash-select block"
            defaultValue={selectedLink?.[name] ?? ''} required><option value="">Choose node</option>
            {activeGraph.nodes.map(node => <option key={node.id} value={node.id}>{node.label}</option>)}</select></label>)}
          <label className="text-sm">Source port<input name="source_port" className="dash-select block" defaultValue={selectedLink?.source_port} /></label>
          <label className="text-sm">Target port<input name="target_port" className="dash-select block" defaultValue={selectedLink?.target_port} /></label>
          <button type="submit" className="ops-button">{selectedLink ? 'Save link' : 'Add link'}</button>
        </form>}
      </details>
        </aside>
      </div>
    </section>}
    {id && <Card title="Changes detected">
      {!changes.length && <p className="ops-muted text-sm">No changes recorded.</p>}
      {changes.map(change => <div className="flex flex-wrap items-center gap-2 py-1 text-sm" key={change.id}>
        <span>{change.description}</span><Badge status={change.decision} />
        {change.decision === 'pending' && (['approved', 'ignored'] as const).map(decision =>
          <button className="ops-button" key={decision} onClick={() => void mutate(`${endpoint}/changes/${change.id}`, 'PATCH', { decision })}>{decision === 'approved' ? 'Approve' : 'Ignore'}</button>)}
      </div>)}
    </Card>}
  </section>;
}

type WidgetSettings = { diagram_id: number | null; site_id: number | null; problems_only: boolean; labels: boolean; height: number };
const defaultWidget: WidgetSettings = { diagram_id: null, site_id: null, problems_only: false, labels: true, height: 340 };

export function NetworkDiagramWidget() {
  const [items, setItems] = useState<DiagramItem[]>([]);
  const [sites, setSites] = useState<Site[]>([]);
  const [current, setCurrent] = useState<{ settings: WidgetSettings | null; diagram: Diagram | null }>();
  const [draft, setDraft] = useState<WidgetSettings>(defaultWidget);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  useAddonUpdates(() => setRefresh(value => value + 1));
  useEffect(() => {
    void Promise.all([request<DiagramItem[]>('/network-widgets/published'), api.getSites()])
      .then(([published, locations]) => { setItems(published); setSites(locations); })
      .catch(failure => setError(failure.message));
  }, [refresh]);
  useEffect(() => {
    const controller = new AbortController();
    void request<typeof current>('/network-widgets/mine', { signal: controller.signal }).then(setCurrent)
      .catch(failure => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [refresh]);

  if (!current && !error) return null;
  if (!items.length && !current?.settings && !error) return null;
  return <Card title="Network Diagram (Live)">
    {error && <p role="alert" className="ops-notice">{error}</p>}
    {current?.settings && !current.diagram && <p className="ops-muted">This diagram is unpublished or unavailable.</p>}
    {current?.diagram && (current.diagram.nodes.length ? <Suspense fallback={<p role="status">Loading diagram…</p>}>
      <Graph diagram={current.diagram} height={current.settings?.height} labels={current.settings?.labels} />
    </Suspense> : <p className="ops-empty">No devices match this widget’s filters.</p>)}
    <details>
      <summary className="cursor-pointer" onClick={() => setDraft(current?.settings ?? defaultWidget)}>
        {current?.settings ? 'Edit diagram widget' : 'Add diagram widget'}
      </summary>
      <form className="flex flex-wrap items-end gap-3 py-2" onSubmit={async event => {
        event.preventDefault();
        try {
          await request('/network-widgets/mine', { method: 'PUT', body: JSON.stringify(draft) });
          setError(''); setRefresh(value => value + 1);
        } catch (failure) { setError((failure as Error).message); }
      }}>
        <label className="text-sm">Published diagram<select className="dash-select block" value={draft.diagram_id ?? ''} onChange={e => setDraft({ ...draft, diagram_id: Number(e.target.value) || null })}>
          <option value="">Remove widget</option>{items.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>
        <label className="text-sm">Site<select className="dash-select block" value={draft.site_id ?? ''} onChange={e => setDraft({ ...draft, site_id: Number(e.target.value) || null })}>
          <option value="">All sites</option>{sites.map(site => <option key={site.id} value={site.id}>{site.name}</option>)}
        </select></label>
        <label className="text-sm">Height<input className="dash-select block w-24" type="number" min={200} max={900} required value={draft.height} onChange={e => setDraft({ ...draft, height: Number(e.target.value) })} /></label>
        <label><input type="checkbox" checked={draft.problems_only} onChange={e => setDraft({ ...draft, problems_only: e.target.checked })} /> Problems only</label>
        <label><input type="checkbox" checked={draft.labels} onChange={e => setDraft({ ...draft, labels: e.target.checked })} /> Labels</label>
        <button type="submit" className="ops-button">Save widget</button>
      </form>
    </details>
  </Card>;
}
