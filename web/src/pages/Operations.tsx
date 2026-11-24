import { lazy, Suspense, useEffect, useState } from 'react';
import { request, api } from '../services/api';
import { useAuth } from '../context/AuthContext';
import { MetricChart, type MetricPoint } from '../components/ui/MetricChart';
import type { Diagram } from '../components/ui/NetworkGraph';
import { NetworkDiagramWidget } from './NetworkDiagram';
import './operations.css';

const Graph = lazy(() => import('../components/ui/NetworkGraph'));
const TrafficDonut = lazy(() => import('../components/ui/TrafficDonut'));
export type Port = { index: string; name: string; alias?: string; status: string; fresh: boolean; speed?: number;
  connector_present?: boolean | null; interface_type?: string; rx_bps?: number | null; tx_bps?: number | null;
  at?: string; pvid?: number; speed_test?: Record<string, {value: number; at: string; reason?: string}> };
export type InterfaceDevice = { id: number; name: string; ip: string; category: string; status: string; observed_at?: string; interfaces: Port[] };
type Widget = { id?: number; title: string; kind: string; device_id?: number; metric_key?: string; diagram_id?: number;
  interfaces: string[]; minutes: number; unit: string; width: 1 | 2; order?: number };
type Board = { id: number; title: string; pinned: boolean };

export function usePolling<T>(path: string | null, interval = 15000) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState('');
  useEffect(() => {
    if (!path) { setData(undefined); return; }
    const controller = new AbortController();
    let timer: number | undefined;
    setData(undefined); setError('');
    async function load() {
      try {
        const value = await request<T>(path!, { signal: controller.signal });
        if (!controller.signal.aborted) { setData(value); setError(''); }
      } catch (failure) {
        if (!controller.signal.aborted) setError((failure as Error).message);
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(load, interval);
      }
    }
    void load();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [path, interval]);
  return { data, error };
}

function ErrorNotice({ error }: { error: string }) { return error ? <p role="alert" className="ops-notice">{error}</p> : null; }
const time = (value?: string) => value ? new Date(value.endsWith('Z') ? value : value+'Z').toLocaleString() : 'Not measured';
export const bits = (value?: number | null) => value == null ? 'Not measured' : `${new Intl.NumberFormat(undefined, {notation:'compact', maximumFractionDigits:1}).format(value)} bps`;

export function EndpointTrace() {
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => { const timer = window.setTimeout(() => {setSearch(query); setPage(0);}, 300); return () => window.clearTimeout(timer); }, [query]);
  const { data, error } = usePolling<{entries: Array<{device: string; device_ip: string; mac: string; ips: string[]; username?: string; port: string; fresh: boolean; observed_at: string; evidence: string; association?: string}>;
    sources: Array<{device: string; observed_at?: string; entries: number}>; note: string; limited: boolean}>(`/dashboard-tools/endpoints?q=${encodeURIComponent(search)}&refresh=${refresh}`);
  return <div className="operations-workspace space-y-4"><header className="dash-top"><div><h1>Live endpoint tracing</h1><p className="ops-muted">SNMP forwarding and ARP evidence · local correlation · no AI or cloud lookup</p></div>
    <button className="ops-button" onClick={() => setRefresh(value => value+1)}>Refresh view</button></header>
    <input className="ops-input w-full" aria-label="Search endpoint by username, MAC or IP" placeholder="Search username, MAC address, or IP address" value={query} onChange={e => setQuery(e.target.value)} />
    <ErrorNotice error={error} />
    {!data && !error && <p role="status">Loading endpoint observations…</p>}
    {data && <><p className="ops-muted text-xs">{data.entries.length} observations · {data.sources.length} reporting devices · refreshes every 15 seconds</p>
      <div className="overflow-x-auto dash-panel"><table className="ops-data-table w-full"><thead><tr>{['Username','IP address','MAC address','Observed on','Port','Evidence','Last observed'].map(label => <th key={label}>{label}</th>)}</tr></thead>
        <tbody>{data.entries.slice(page*100,(page+1)*100).map((row,index) => <tr key={`${row.device}-${row.mac}-${index}`}><td>{row.username || 'Not reported'}</td><td>{row.ips.join(', ') || 'ARP not reported'}</td><td className="font-mono">{row.mac}</td>
          <td>{row.device}<div className="ops-muted text-xs">{row.device_ip}</div></td><td>{row.port}<div className="ops-muted text-xs">{row.association}</div></td><td>{row.evidence}<div className="text-xs ops-muted">{row.fresh ? 'Recent observation' : 'Stale observation'}</div></td><td>{time(row.observed_at)}</td></tr>)}</tbody></table>
        {!data.entries.length && <p className="ops-empty">No matching forwarding observations. SNMP views must expose forwarding and ARP tables; not all firewalls provide a switching table.</p>}</div>
      {data.entries.length>100 && <div className="flex gap-3"><button className="ops-button" disabled={!page} onClick={() => setPage(value => value-1)}>Previous</button><span>Page {page+1}</span><button className="ops-button" disabled={(page+1)*100>=data.entries.length} onClick={() => setPage(value => value+1)}>Next</button></div>}
      <p className="ops-notice">{data.note}</p>{data.limited && <p role="status">Results capped at 2,000 entries. Narrow your search.</p>}
      <details><summary>Collection sources</summary><ul>{data.sources.map((source,index) => <li key={index}>{source.device} · {source.entries} entries · {time(source.observed_at)}</li>)}</ul></details></>}
  </div>;
}

export function PortLayout({ deviceId }: { deviceId: number }) {
  const {data, error} = usePolling<InterfaceDevice[]>(`/dashboard-tools/interfaces?device_id=${deviceId}`, 30000);
  const [selectedIndex, setSelectedIndex] = useState<string>();
  const device = data?.[0];
  const selected=device?.interfaces.find(port=>port.index===selectedIndex);
  if (device && !['Network','Routers','Firewalls'].includes(device.category)) return null;
  const physical = device?.interfaces.filter(port => port.connector_present === true) ?? [];
  const known = device?.interfaces.some(port => port.connector_present != null);
  const ports = known ? physical : device?.interfaces.filter(port => port.interface_type === '6') ?? [];
  return <section className="dash-panel"><header className="dash-panel-head"><div><h2>Device port layout</h2><p>{known ? `${ports.length} physical connectors reported by SNMP` : 'Physical connector count unavailable · showing observed Ethernet interfaces'}</p></div></header>
    <div className="dash-panel-body"><ErrorNotice error={error}/>{!data && !error && <p role="status">Loading reported port inventory…</p>}
      <div className="physical-ports">{ports.map(port => <button key={port.index} className="physical-port" data-up={port.status==='up' && port.fresh} aria-label={`${port.name}: ${port.status}`} title={`${port.name} · ${port.status} · ${bits(port.speed)}`} onClick={() => setSelectedIndex(port.index)}>
        <span aria-hidden="true" className="port-socket"/><span>{port.name}</span></button>)}</div>
      {data && !ports.length && <p className="ops-empty">This agent has not reported physical port inventory. No fixed or guessed port count is drawn.</p>}
      <p className="ops-muted text-xs">Green: online · Gray: down, unused, or unknown. Read-only visualization.</p>
      {selected && <dl className="grid grid-cols-2 gap-2 text-sm"><dt>Port</dt><dd>{selected.name}</dd><dt>SNMP index</dt><dd>{selected.index}</dd><dt>Status</dt><dd>{selected.status}</dd><dt>Reported link capacity</dt><dd>{bits(selected.speed)}</dd><dt>Description</dt><dd>{selected.alias || 'Not reported'}</dd><dt>Last observed</dt><dd>{time(selected.at)}</dd></dl>}
    </div></section>;
}

export function trafficShares(ports: Port[]) {
  const measured = ports.filter(port => port.fresh && port.rx_bps != null && port.tx_bps != null);
  const data = measured.map(port => ({name: port.name, value: Math.max(0,port.rx_bps!)+Math.max(0,port.tx_bps!)}));
  const total = data.reduce((sum,row) => sum+row.value,0);
  return {data, total, measured: measured.length, percentages: data.map(row => ({...row, share: total ? row.value/total*100 : 0}))};
}

function InterfaceWidget({widget}: {widget: Widget}) {
  const {data,error} = usePolling<InterfaceDevice[]>(`/dashboard-tools/interfaces?device_id=${widget.device_id}`);
  const device = data?.[0];
  const ports = device?.interfaces.filter(port => widget.interfaces.includes(port.index)) ?? [];
  const shares = trafficShares(ports);
  return <><ErrorNotice error={error}/>{!device && !error && <p>Loading interface observations…</p>}
    {!widget.interfaces.length && <p className="ops-empty">Select the WAN interfaces in widget settings. LAN traffic is not automatically labelled internet usage.</p>}
    {widget.kind==='firewall_usage' && ports.length>0 && <><p className="text-xs ops-muted">Latest SNMP traffic rate share (receive + transmit) · {shares.measured}/{ports.length} links reporting</p>
      {shares.total>0 ? <Suspense fallback={<p>Loading chart…</p>}><TrafficDonut data={shares.data}/></Suspense> : <p className="ops-empty">{shares.measured ? 'No traffic in the last measured interval.' : 'Waiting for two valid counter snapshots; no usage percentage is inferred.'}</p>}
      <ul className="text-sm">{shares.percentages.map(row => <li key={row.name}>{row.name} · {row.share.toFixed(1)}% · {bits(row.value)}</li>)}</ul></>}
    {widget.kind==='interfaces' && <div className="wan-links">{ports.map(port => <article className="wan-link" key={port.index}>
      <strong>{port.name}</strong><span>{port.fresh ? port.status==='up' ? 'Link up' : port.status==='down' ? 'Link down' : 'Link unknown' : 'Observation stale'}</span>
      <p>RX {bits(port.fresh ? port.rx_bps : null)} · TX {bits(port.fresh ? port.tx_bps : null)}</p>
      <p className="ops-muted">Port capacity {bits(port.speed)} · internet reachability is not inferred from link status</p>
      <p>Last download test: {bits(port.speed_test?.download_bps?.value)}</p><p>Last upload test: {bits(port.speed_test?.upload_bps?.value)}</p>
      <p className="ops-muted">Measured {time(port.speed_test?.download_bps?.at)}</p>
      {port.speed_test?.ok && <p>Last attempt {time(port.speed_test.ok.at)} · {port.speed_test.ok.value===1 ? 'Completed' : port.speed_test.ok.reason || 'Failed'}</p>}
    </article>)}</div>}
  </>;
}

function MetricWidget({widget}: {widget: Widget}) {
  const [points,setPoints] = useState<MetricPoint[]>();
  const [error,setError] = useState('');
  useEffect(() => {
    const controller = new AbortController(); let timer: number | undefined;
    async function load() {
      try {
        const end = new Date(); const start = new Date(end.getTime()-widget.minutes*60000);
        const result = await api.queryMetrics({device_id:widget.device_id!,metric_keys:[widget.metric_key!],start_time:start.toISOString(),end_time:end.toISOString()},controller.signal);
        if (!controller.signal.aborted) {setPoints(result.series.flatMap(series => series.points));setError('');}
      } catch(failure) {if (!controller.signal.aborted) setError((failure as Error).message);}
      finally {if(!controller.signal.aborted) timer=window.setTimeout(load,30000);}
    }
    void load(); return () => {controller.abort();window.clearTimeout(timer);};
  },[widget.device_id,widget.metric_key,widget.minutes]);
  const last = points?.length ? points[points.length-1] : undefined; const value = last?.value;
  return <><ErrorNotice error={error}/>{!points && !error ? <p>Loading telemetry…</p> : widget.kind==='stat' ? <div><strong className="text-3xl">{value==null ? 'No data' : value.toLocaleString()} {value==null ? '' : widget.unit}</strong><p className="ops-muted text-xs">{time(last?.timestamp)}</p></div> : <MetricChart title={widget.title} unit={widget.unit} data={points} height={180}/>}</>;
}

function DiagramWidget({identifier}: {identifier:number}) {
  const {data,error}=usePolling<Diagram>(`/network-diagram/${identifier}`,30000);
  const [labels,setLabels]=useState(false);const [selected,setSelected]=useState<number>();
  const node=data?.nodes.find(row=>row.id===selected);
  return <><ErrorNotice error={error}/>{data ? <><div className="flex justify-between gap-2 text-xs ops-muted"><span>{data.nodes.length} devices · {data.links.length} links · scroll to zoom, drag to pan</span><label><input type="checkbox" checked={labels} onChange={e=>setLabels(e.target.checked)}/> Show names</label></div><Suspense fallback={<p>Loading diagram…</p>}><Graph diagram={data} height={320} labels={labels} onSelect={(kind,id)=>{if(kind==='node')setSelected(id);}} selectedNodeId={selected}/></Suspense>{node&&<p className="ops-notice">{node.label} · {node.status} · {node.ip||'IP not reported'}</p>}</> : !error && <p>Loading diagram observations…</p>}</>;
}

function FeedWidget({kind}: {kind:string}) {
  const {data,error}=usePolling<Array<{id:number;title:string;severity:string}>>(kind==='events' ? '/events?limit=8' : '/alerts?status=active&limit=8',30000);
  return <><ErrorNotice error={error}/>{data?.length ? <ul>{data.map(row=><li key={row.id}>{row.title} · {row.severity}</li>)}</ul> : <p className="ops-muted">{data ? 'No matching records.' : 'Loading records…'}</p>}</>;
}

function WidgetBody({widget}:{widget:Widget}) {
  if(widget.kind==='diagram') return <DiagramWidget identifier={widget.diagram_id!}/>;
  if(widget.kind==='interfaces' || widget.kind==='firewall_usage') return <InterfaceWidget widget={widget}/>;
  if(widget.kind==='events' || widget.kind==='alerts') return <FeedWidget kind={widget.kind}/>;
  return <MetricWidget widget={widget}/>;
}

export function PinnedDashboards() {
  const {user}=useAuth();
  const {data}=usePolling<Board[]>(user?.role==='admin' ? '/dashboard-tools/dashboards' : null,30000);
  return <>{data?.filter(board=>board.pinned).map(board=><DashboardCanvas key={board.id} board={board}/>)}</>;
}
function DashboardCanvas({board}:{board:Board}) {
  const {data,error}=usePolling<Widget[]>(`/dashboard-tools/dashboards/${board.id}/widgets`,30000);
  return <section className="operations-workspace space-y-2" aria-label={board.title}><h2>{board.title}</h2><ErrorNotice error={error}/>{!data&&!error&&<p role="status">Loading saved widgets…</p>}{data&&!data.length&&<p className="ops-muted">No widgets saved on this dashboard.</p>}<div className="custom-widget-grid">{data?.sort((a,b)=>(a.order??0)-(b.order??0)).map(widget=><article key={widget.id} className="dash-panel custom-widget" data-wide={widget.width===2}><header className="dash-panel-head"><h3>{widget.title}</h3></header><div className="dash-panel-body"><WidgetBody widget={widget}/></div></article>)}</div></section>;
}

export function AutomaticOperationsDiagram() {
  const {user}=useAuth();
  const {data:features}=usePolling<{network_diagram_beta:boolean}>('/features',60000);
  const {data:mine}=usePolling<{settings:unknown;diagram:Diagram|null}>(features?.network_diagram_beta ? '/network-widgets/mine' : null,30000);
  const {data:list,error}=usePolling<Array<{id:number;automatic:boolean}>>(features?.network_diagram_beta && user?.role==='admin' && !mine?.settings ? '/network-diagram' : null,60000);
  const automatic=list?.find(row=>row.automatic);
  if(!features?.network_diagram_beta) return null;
  if(mine?.settings) return <NetworkDiagramWidget/>;
  return <section className="dash-panel"><header className="dash-panel-head"><div><h2>Infrastructure topology</h2><p>Automatic discovery · device status and live link activity</p></div></header><div className="dash-panel-body"><ErrorNotice error={error}/>
    {automatic ? <DiagramWidget identifier={automatic.id}/> : <p className="ops-muted">{user?.role==='admin' ? 'Waiting for an automatic SNMP diagram. The separate Network Diagram page remains available.' : 'A published diagram can be pinned by an administrator.'}</p>}</div></section>;
}

const emptyWidget: Widget={title:'Monitored item',kind:'metric',interfaces:[],minutes:60,unit:'',width:1};
export function CustomDashboards() {
  const [boards,setBoards]=useState<Board[]>([]); const [selected,setSelected]=useState<number>();
  const [name,setName]=useState('Operations'); const [widgets,setWidgets]=useState<Widget[]>([]);
  const [draft,setDraft]=useState<Widget>(emptyWidget); const [error,setError]=useState(''); const [notice,setNotice]=useState('');
  const [dragged,setDragged]=useState<number>(); const [widgetsLoading,setWidgetsLoading]=useState(false);
  const {data:devices}=usePolling<InterfaceDevice[]>('/dashboard-tools/interfaces',60000);
  const {data:diagrams}=usePolling<Array<{id:number;name:string}>>('/dashboard-tools/diagrams',60000);
  const [metrics,setMetrics]=useState<Array<{metric_key:string}>>([]);
  const loadBoards=async()=>{const rows=await request<Board[]>('/dashboard-tools/dashboards');setBoards(rows);return rows;};
  useEffect(()=>{void loadBoards().then(rows=>setSelected(rows[0]?.id)).catch(failure=>setError(failure.message));},[]);
  useEffect(()=>{if(!selected)return; setWidgetsLoading(true);setWidgets([]);const controller=new AbortController();void request<Widget[]>(`/dashboard-tools/dashboards/${selected}/widgets`,{signal:controller.signal}).then(rows=>setWidgets(rows.sort((a,b)=>(a.order??0)-(b.order??0)))).catch(failure=>{if(!controller.signal.aborted)setError(failure.message);}).finally(()=>{if(!controller.signal.aborted)setWidgetsLoading(false);});return()=>controller.abort();},[selected]);
  useEffect(()=>{let active=true;setMetrics([]);if(draft.device_id)void api.getLatestMetrics(draft.device_id).then(rows=>{if(active)setMetrics(rows);}).catch(failure=>{if(active)setError(failure.message);});return()=>{active=false;};},[draft.device_id]);
  function move(from:number,to:number){if(to<0||to>=widgets.length)return;setWidgets(previous=>{const rows=[...previous];const [item]=rows.splice(from,1);rows.splice(to,0,item);return rows;});setNotice('Layout changed. Save to persist.');}
  const board=boards.find(row=>row.id===selected); const device=devices?.find(row=>row.id===draft.device_id);
  return <div className="operations-workspace space-y-4"><header className="dash-top"><div><h1>Custom dashboards</h1><p className="ops-muted">Existing monitored items · saved in MongoDB · drag widgets or use position controls</p></div></header><ErrorNotice error={error}/>{notice&&<p role="status">{notice}</p>}
    <div className="dash-panel p-3 flex flex-wrap gap-2 items-center"><label>Dashboard <select aria-label="Selected custom dashboard" className="dash-select" value={selected??''} onChange={e=>setSelected(Number(e.target.value))}><option value="">Select dashboard</option>{boards.map(row=><option key={row.id} value={row.id}>{row.title}</option>)}</select></label>
      <input aria-label="New dashboard name" className="ops-input" value={name} onChange={e=>setName(e.target.value)} maxLength={128}/><button className="ops-button" onClick={async()=>{try{const row=await request<Board>('/dashboard-tools/dashboards',{method:'POST',body:JSON.stringify({title:name,pinned:false})});await loadBoards();setSelected(row.id);}catch(failure){setError((failure as Error).message);}}}>Create dashboard</button>
      {board&&<><label><input type="checkbox" checked={board.pinned} onChange={async e=>{const pinned=e.target.checked;setBoards(rows=>rows.map(row=>row.id===board.id?{...row,pinned}:row));try{await request(`/dashboard-tools/dashboards/${board.id}`,{method:'PUT',body:JSON.stringify({...board,pinned})});await loadBoards();}catch(failure){setError((failure as Error).message);}}}/> Pin to Overview</label><button className="ops-button" disabled={widgetsLoading} onClick={async()=>{try{await request(`/dashboard-tools/dashboards/${selected}/widgets`,{method:'PUT',body:JSON.stringify(widgets)});setNotice('Dashboard saved.');setError('');}catch(failure){setError((failure as Error).message);}}}>Save layout</button><button className="ops-button" onClick={async()=>{if(!window.confirm('Delete this custom dashboard and its widgets?'))return;try{await request(`/dashboard-tools/dashboards/${board.id}`,{method:'DELETE'});const rows=await loadBoards();setSelected(rows[0]?.id);setWidgets([]);}catch(failure){setError((failure as Error).message);}}}>Delete dashboard</button></>}
    </div>
    {widgetsLoading&&<p role="status">Loading saved widgets…</p>}{selected&&!widgetsLoading&&<details className="dash-panel p-3" open><summary>Add a monitored widget</summary><form className="widget-settings" onSubmit={e=>{e.preventDefault();setWidgets(rows=>[...rows,{...draft,id:Date.now()}]);setNotice('Widget added. Save layout to persist.');}}>
      <label>Title<input className="ops-input" value={draft.title} onChange={e=>setDraft({...draft,title:e.target.value})} required maxLength={128}/></label>
      <label>Widget type<select aria-label="Widget type" className="dash-select" value={draft.kind} onChange={e=>setDraft({...draft,kind:e.target.value,interfaces:[],width:e.target.value==='diagram'?2:1})}>{[['metric','Time series'],['stat','Latest value'],['diagram','Network diagram'],['interfaces','Firewall / interface connectivity'],['firewall_usage','Firewall WAN usage donut'],['alerts','Active alerts'],['events','Recent events']].map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
      {draft.kind==='diagram' ? <label>Diagram<select aria-label="Widget diagram" className="dash-select" required value={draft.diagram_id??''} onChange={e=>setDraft({...draft,diagram_id:Number(e.target.value)})}><option value="">Select existing diagram</option>{diagrams?.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}</select></label> : !['alerts','events'].includes(draft.kind)&&<label>Device<select aria-label="Widget device" className="dash-select" required value={draft.device_id??''} onChange={e=>setDraft({...draft,device_id:Number(e.target.value),metric_key:undefined,interfaces:[]})}><option value="">Select monitored device</option>{devices?.filter(row=>!['firewall_usage'].includes(draft.kind)||row.category==='Firewalls').map(row=><option key={row.id} value={row.id}>{row.name} ({row.ip})</option>)}</select></label>}
      {['metric','stat'].includes(draft.kind)&&<><label>Monitored metric<select aria-label="Widget metric" className="dash-select" required value={draft.metric_key??''} onChange={e=>setDraft({...draft,metric_key:e.target.value})}><option value="">Select reported metric</option>{metrics.map(row=><option key={row.metric_key}>{row.metric_key}</option>)}</select></label><label>Units<input className="ops-input" value={draft.unit} onChange={e=>setDraft({...draft,unit:e.target.value})} maxLength={32}/></label><label>Time range<select className="dash-select" value={draft.minutes} onChange={e=>setDraft({...draft,minutes:Number(e.target.value)})}>{[5,15,60,360,1440,10080].map(minutes=><option key={minutes} value={minutes}>{minutes} minutes</option>)}</select></label></>}
      {['interfaces','firewall_usage'].includes(draft.kind)&&<fieldset><legend>Select interfaces (choose WAN links explicitly)</legend>{device?.interfaces.map(port=><label key={port.index} className="inline-flex gap-1 mr-3"><input type="checkbox" checked={draft.interfaces.includes(port.index)} onChange={e=>setDraft({...draft,interfaces:e.target.checked?[...draft.interfaces,port.index]:draft.interfaces.filter(index=>index!==port.index)})}/>{port.name}</label>)}{device&&!device.interfaces.length&&<p>No interface inventory reported yet.</p>}</fieldset>}
      <label>Width<select className="dash-select" value={draft.width} onChange={e=>setDraft({...draft,width:Number(e.target.value) as 1|2})}><option value={1}>Compact</option><option value={2}>Full width</option></select></label><button className="ops-button" type="submit">Add widget</button>
    </form></details>}
    <div className="custom-widget-grid">{widgets.map((widget,index)=><article key={widget.id??index} className="dash-panel custom-widget" data-wide={widget.width===2} onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();if(dragged!=null)move(dragged,index);setDragged(undefined);}}>
      <header className="dash-panel-head"><input aria-label={`Widget ${index+1} title`} className="ops-input min-w-0" value={widget.title} maxLength={128} onChange={e=>setWidgets(rows=>rows.map((row,at)=>at===index?{...row,title:e.target.value}:row))}/><div className="flex gap-1 flex-wrap"><button className="ops-button" aria-label={`Resize ${widget.title}`} onClick={()=>setWidgets(rows=>rows.map((row,at)=>at===index?{...row,width:row.width===1?2:1}:row))}>{widget.width===1?'Widen':'Compact'}</button><button draggable onDragStart={()=>setDragged(index)} className="ops-button" aria-label={`Drag ${widget.title}`}>↕</button><button className="ops-button" aria-label={`Move ${widget.title} up`} onClick={()=>move(index,index-1)} disabled={!index}>↑</button><button className="ops-button" aria-label={`Move ${widget.title} down`} onClick={()=>move(index,index+1)} disabled={index===widgets.length-1}>↓</button><button className="ops-button" aria-label={`Remove ${widget.title}`} onClick={()=>setWidgets(rows=>rows.filter((_,at)=>at!==index))}>Remove</button></div></header><div className="dash-panel-body"><WidgetBody widget={widget}/></div></article>)}</div>
    {device?.category==='Firewalls'&&<WanTestSettings device={device}/>}
  </div>;
}

type Target={index:string;name:string;source_ip:string;download_url:string;upload_url?:string|null};
type WanSettings={enabled:boolean;interval_seconds:number;max_bytes:number;targets:Target[]};
function WanTestSettings({device}:{device:InterfaceDevice}) {
  const [config,setConfig]=useState<WanSettings>({enabled:false,interval_seconds:900,max_bytes:10000000,targets:[]});
  const [error,setError]=useState('');const [notice,setNotice]=useState('');
  useEffect(()=>{const controller=new AbortController();void request<WanSettings>(`/dashboard-tools/wan-tests/${device.id}`,{signal:controller.signal}).then(setConfig).catch(failure=>{if(!controller.signal.aborted)setError(failure.message);});return()=>controller.abort();},[device.id]);
  return <details className="dash-panel p-3"><summary>Rotating WAN speed tests · {device.name}</summary><p className="ops-notice">Source IPs must exist on the assigned collector, with routing forcing each source through its selected WAN. Test servers must be reachable; a speed test requires network traffic and cannot measure an offline internet link. Downloads are capped at {config.max_bytes.toLocaleString()} bytes, uploads at 2 MB. Tests run serially; each configured WAN is tested once per full rotation ({config.targets.length*config.interval_seconds} seconds).</p><ErrorNotice error={error}/>{notice&&<p role="status">{notice}</p>}
    <form className="space-y-3" onSubmit={async e=>{e.preventDefault();try{await request(`/dashboard-tools/wan-tests/${device.id}`,{method:'PUT',body:JSON.stringify(config)});setNotice('Rotating test schedule saved.');setError('');}catch(failure){setError((failure as Error).message);}}}>
      <label><input type="checkbox" checked={config.enabled} onChange={e=>setConfig({...config,enabled:e.target.checked})}/> Enable scheduled network tests</label><label> Seconds between tests <input aria-label="WAN test interval" className="ops-input" type="number" min={300} max={86400} value={config.interval_seconds} onChange={e=>setConfig({...config,interval_seconds:Number(e.target.value)})}/></label>
      {config.targets.map((target,index)=><div className="widget-settings" key={index}><label>Interface<select aria-label={`WAN interface ${index+1}`} className="dash-select" required value={target.index} onChange={e=>{const port=device.interfaces.find(row=>row.index===e.target.value);setConfig({...config,targets:config.targets.map((row,at)=>at===index?{...row,index:e.target.value,name:port?.name??e.target.value}:row)});}}><option value="">Select WAN</option>{device.interfaces.map(port=><option key={port.index} value={port.index}>{port.name}</option>)}</select></label>
        {(['source_ip','download_url','upload_url'] as const).map(field=><label key={field}>{field.replace(/_/g,' ')}<input aria-label={`WAN ${index+1} ${field}`} className="ops-input" required={field!=='upload_url'} value={target[field]??''} type={field==='source_ip'?'text':'url'} onChange={e=>setConfig({...config,targets:config.targets.map((row,at)=>at===index?{...row,[field]:e.target.value||null}:row)})}/></label>)}
        <button className="ops-button" type="button" onClick={()=>setConfig({...config,targets:config.targets.filter((_,at)=>at!==index)})}>Remove target</button></div>)}
      <button className="ops-button" type="button" disabled={config.targets.length>=16} onClick={()=>setConfig({...config,targets:[...config.targets,{index:'',name:'WAN',source_ip:'',download_url:'',upload_url:null}]})}>Add WAN test target</button><button className="ops-button" type="submit">Save test schedule</button>
    </form></details>;
}
