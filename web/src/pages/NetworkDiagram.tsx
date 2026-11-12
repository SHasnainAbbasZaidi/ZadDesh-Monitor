import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Card } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import type { Diagram } from '../components/ui/NetworkGraph';
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
      if (!controller.signal.aborted) { setGraph(diagram); setRuns(runItems); setChanges(changeItems); }
    }).catch(failure => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [endpoint, id, refresh]);

  async function mutate(path: string, method: string, body?: object) {
    try {
      setError('');
      await request(path, { method, body: body ? JSON.stringify(body) : undefined });
      setRefresh(value => value + 1);
    } catch (failure) { setError((failure as Error).message); }
  }

  const activeGraph = graph?.id === id ? graph : undefined;
  const visible = activeGraph && search.trim() ? activeGraph.nodes.filter(node => node.label.toLowerCase().includes(search.trim().toLowerCase())) : activeGraph?.nodes;

  return <section className="space-y-4">
    <h1 className="ops-heading">Network Diagram <Badge status="info">Beta</Badge></h1>
    <p className="ops-muted text-sm">The automatic map uses saved SNMPv3 checks on permitted devices and refreshes in the background.</p>
    {error && <p className="ops-notice" role="alert">{error}</p>}
    <Card><div className="flex flex-wrap items-end gap-3">
      <label className="text-sm">Diagram<select className="dash-select block" value={id ?? ''} onChange={event => {
        choseDiagram.current = true;
        setId(Number(event.target.value)); setPositions({}); setSelection(undefined); setSearch('');
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
    {id && <Card title="Discovery">
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
    </Card>}
    {activeGraph && <Card title={activeGraph.name}>
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs">Layout<select className="dash-select block" value={activeGraph.layout.mode ?? 'hierarchical'} onChange={e => {
          setPositions({}); void mutate(endpoint, 'PATCH', { layout: e.target.value, positions: {} });
        }}>{layouts.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <button className="ops-button" onClick={() => setLocked(!locked)}>{locked ? 'Unlock layout' : 'Lock layout'}</button>
        <button className="ops-button" disabled={!Object.keys(positions).length}
          onClick={() => void mutate(endpoint, 'PATCH', { positions: { ...activeGraph.layout.positions, ...positions } })}>Save layout</button>
        <button className="ops-button" onClick={() => setFit(value => value + 1)}>Fit to screen</button>
        <button className="ops-button" onClick={() => void mutate(endpoint, 'PATCH', { published: !activeGraph.published })}>{activeGraph.published ? 'Unpublish' : 'Publish'}</button>
        <button className="ops-button" onClick={() => download('network-diagram.json', new Blob([JSON.stringify(activeGraph, null, 2)], { type: 'application/json' }))}>JSON</button>
        <button className="ops-button" disabled={!activeGraph.nodes.length} onClick={async () => {
          const { svgFor } = await import('../components/ui/NetworkGraph');
          download('network-diagram.svg', new Blob([svgFor(activeGraph)], { type: 'image/svg+xml' }));
        }}>SVG</button>
        <button className="ops-button" disabled={!activeGraph.nodes.length} onClick={() => { if (png.current) download('network-diagram.png', png.current()); }}>PNG</button>
      </div>
      {activeGraph.nodes.length ? <Suspense fallback={<p role="status">Loading diagram…</p>}>
        <Graph key={`${id}:${fit}`} diagram={activeGraph} draggable={!locked} onSelect={(kind, item) => setSelection({ kind, id: item })}
          onMove={!locked ? (node, point) => setPositions(previous => ({ ...previous, [node]: point })) : undefined}
          exportGraph={exporter => { png.current = exporter; }} />
      </Suspense> : <p className="ops-empty">{activeGraph.automatic
        ? 'Waiting for successful SNMPv3 discovery results.'
        : 'Run discovery or add a node to populate this diagram.'}</p>}
      <label className="text-sm">Find a node<input className="dash-select ml-2" value={search} onChange={event => setSearch(event.target.value)} /></label>
      <div className="mt-2 flex max-h-28 flex-wrap gap-2 overflow-auto">
        {visible?.map(node => <button className="ops-button" key={node.id} onClick={() => setSelection({ kind: 'node', id: node.id })}>{node.label}</button>)}
      </div>
      {selectedNode && <div className="mt-4 space-y-2" aria-label="Node details">
        <h3 className="font-semibold">{selectedNode.label} · {selectedNode.kind}</h3>
        <p><Badge status={selectedNode.status} /> {selectedNode.ip} · {selectedNode.alerts} active alerts</p>
        <div className="flex flex-wrap gap-2">{activeGraph.links.filter(link => [link.source, link.target].includes(selectedNode.id)).map(link =>
          <button className="ops-button" key={link.id} onClick={() => setSelection({ kind: 'edge', id: link.id })}>{link.source_port} ↔ {link.target_port}</button>)}</div>
      </div>}
      {selectedLink && <div className="mt-4 space-y-2" aria-label="Link details">
        <h3 className="font-semibold">{selectedLink.source_port} ↔ {selectedLink.target_port}</h3>
        <p><Badge status={selectedLink.status} /> {selectedLink.method.toUpperCase()} · {selectedLink.confidence} confidence
          {selectedLink.speed != null && ` · ${selectedLink.speed.toLocaleString()} bps`}
          {selectedLink.utilization != null && ` · ${selectedLink.utilization.toFixed(1)}% utilization`}</p>
        <button className="ops-button" onClick={() => void mutate(`${endpoint}/links/${selectedLink.id}`, 'DELETE')}>Remove link</button>
      </div>}
      <details className="mt-4"><summary className="cursor-pointer">Manual edits</summary>
        <form className="my-2 flex flex-wrap items-end gap-2" key={selectedNode?.id ?? 'new'} onSubmit={event => {
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
        {activeGraph.nodes.length > 1 && <form className="flex flex-wrap items-end gap-2" key={selectedLink?.id ?? 'new'} onSubmit={event => {
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
    </Card>}
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
