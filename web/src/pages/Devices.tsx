import React, { useEffect, useState } from 'react';
import { ZabbixAddHostButton, ZabbixDevices, zabbixAvailability, type ZabbixInventoryHost } from '../components/ui/Zabbix';
import { Card } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Modal } from '../components/ui/Modal';
import { MetricChart } from '../components/ui/MetricChart';
import { DefaultSnmpFields, readSnmpInterface, SnmpFields } from '../components/ui/SnmpCheckForm';
import { useAuth } from '../context/AuthContext';
import { api, request } from '../services/api';
import { Device, Site, Group, Check } from '../types';
import {
  Plus,
  Server,
  Trash2,
  RefreshCw,
  Search,
  CheckCircle,
  AlertCircle,
  ExternalLink,
  Activity,
  Cpu,
  Clock,
  Shield,
} from 'lucide-react';

interface DevicesProps {
  searchQuery?: string;
}

interface MetricSeriesData {
  timestamp: string;
  value: number;
}

export const Devices: React.FC<DevicesProps> = ({ searchQuery = '' }) => {
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const [loadError, setLoadError] = useState('');
  const [telemetryError, setTelemetryError] = useState('');
  const [httpMetrics, setHttpMetrics] = useState<MetricSeriesData[]>([]);
  const [tcpMetrics, setTcpMetrics] = useState<MetricSeriesData[]>([]);
  const [snmpMetrics, setSnmpMetrics] = useState<{ key: string; unit: string; points: MetricSeriesData[] }[]>([]);
  const [range, setRange] = useState(120);
  const [devices, setDevices] = useState<Device[]>([]);
  const [showZabbix, setShowZabbix] = useState(false);
  const [zabbixHosts, setZabbixHosts] = useState<ZabbixInventoryHost[]>([]);
  const [zabbixLoading, setZabbixLoading] = useState(isAdmin);
  const [zabbixError, setZabbixError] = useState('');
  const [zabbixRefresh, setZabbixRefresh] = useState(0);
  const [sites, setSites] = useState<Site[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [filterStatus, setFilterStatus] = useState<string>('all');
  const [loading, setLoading] = useState(true);
  const [selection, setSelection] = useState<Set<string>>(new Set());
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState('');

  // Selected Device Drawer / Modal
  const [selectedDevice, setSelectedDevice] = useState<Device | null>(null);
  const [deviceChecks, setDeviceChecks] = useState<Check[]>([]);
  const [rttMetrics, setRttMetrics] = useState<MetricSeriesData[]>([]);
  const [availMetrics, setAvailMetrics] = useState<MetricSeriesData[]>([]);
  const [cpuMetrics, setCpuMetrics] = useState<MetricSeriesData[]>([]);
  const [metricsLoading, setMetricsLoading] = useState(false);

  // Add Device Modal State
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [name, setName] = useState('');
  const [ip, setIp] = useState('');
  const [hostname, setHostname] = useState('');
  const [siteId, setSiteId] = useState<number | undefined>();
  const [groupId, setGroupId] = useState<number | undefined>();
  const [vendor, setVendor] = useState('Generic');
  const [model, setModel] = useState('');
  const [checkTypes, setCheckTypes] = useState<string[]>(['icmp']);
  const [snmpMode, setSnmpMode] = useState('default');
  const [showDeviceDetails, setShowDeviceDetails] = useState(false);
  const defaultSnmp = checkTypes.includes('snmp') && snmpMode === 'default';
  const hideDeviceDetails = defaultSnmp && !showDeviceDetails;
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const loadDevices = async () => {
    try {
      setLoading(true);
      const [dRes, sRes, gRes] = await Promise.all([
        api.getDevices(),
        api.getSites(),
        api.getGroups(),
      ]);
      setLoadError('');
      setDevices(dRes);
      setSites(sRes);
      setGroups(gRes);
    } catch (err) {
      setLoadError('Could not load the device inventory. Retry using Refresh devices.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadDevices();
  }, []);

  useEffect(() => {
    if (!isAdmin) return;
    const controller = new AbortController();
    async function loadInventory() {
      try {
        const result = await request<{ hosts: ZabbixInventoryHost[] }>('/zabbix/inventory', { signal: controller.signal });
        if (!controller.signal.aborted) { setZabbixHosts(result.hosts); setZabbixError(''); }
      } catch (failure) {
        if (!controller.signal.aborted) setZabbixError((failure as Error).message);
      } finally {
        if (!controller.signal.aborted) setZabbixLoading(false);
      }
    }
    void loadInventory();
    const timer = window.setInterval(loadInventory, 60_000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [isAdmin, zabbixRefresh]);

  const selectedId = selectedDevice?.id;
  useEffect(() => {
    if (!selectedId) return;
    let active = true;
    let timer: number | undefined;
    async function fetchTelemetry() {
    try {
      const now = new Date();
      const checks = await api.getChecks({ device_id: selectedId });
      const snmp = checks.some(check => check.check_type === 'snmp')
        ? (await api.getLatestMetrics(selectedId!)).filter(item => item.metric_key.startsWith('snmp.') && item.metric_key !== 'snmp.available').slice(0, 6)
        : [];
      const metricRes = await api.queryMetrics({
          device_id: selectedId!,
          metric_keys: ['icmp.rtt_avg', 'icmp.availability', 'cpu.utilization', 'http.response_time_ms', 'tcp.connect_time_ms', ...snmp.map(item => item.metric_key)],
          start_time: new Date(now.getTime() - range * 60_000).toISOString(),
          end_time: now.toISOString(),
      });
      if (!active) return;
      setDeviceChecks(checks);
      setSnmpMetrics(snmp.map(item => ({ key: item.metric_key, unit: item.tags?.unit ?? 'value', points: metricRes.series.find(series => series.metric_key === item.metric_key)?.points ?? [] })));
      setTelemetryError('');
      for (const series of metricRes.series) {
        if (series.metric_key === 'icmp.rtt_avg') setRttMetrics(series.points);
        if (series.metric_key === 'icmp.availability') setAvailMetrics(series.points.map(point => ({ ...point, value: point.value * 100 })));
        if (series.metric_key === 'cpu.utilization') setCpuMetrics(series.points);
        if (series.metric_key === 'http.response_time_ms') setHttpMetrics(series.points);
        if (series.metric_key === 'tcp.connect_time_ms') setTcpMetrics(series.points);
      }
    } catch {
      if (active) setTelemetryError('Could not refresh telemetry from the core.');
    } finally {
      if (active) { setMetricsLoading(false); timer = window.setTimeout(fetchTelemetry, 10_000); }
    }
    }
    void fetchTelemetry();
    return () => { active = false; window.clearTimeout(timer); };
  }, [selectedId, range]);

  const openDeviceDetails = (dev: Device) => {
    setRttMetrics([]); setAvailMetrics([]); setCpuMetrics([]); setHttpMetrics([]); setTcpMetrics([]);
    setDeviceChecks([]); setSnmpMetrics([]); setTelemetryError(''); setMetricsLoading(true); setSelectedDevice(dev);
  };



  const handleCreateDevice = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setFormError(null);
    setIsSubmitting(true);

    try {
      await api.createDevice({
        name: name.trim() || ip.trim(),
        ip: ip.trim(),
        hostname: hostname || undefined,
        site_id: siteId || undefined,
        group_id: groupId || undefined,
        vendor: vendor || undefined,
        model: model || undefined,
        initial_check_types: checkTypes.filter(type => type !== 'snmp'),
        initial_snmp: checkTypes.includes('snmp') ? readSnmpInterface(form) : undefined,
        initial_tcp_port: checkTypes.includes('tcp') ? Number(form.get('tcp-port')) : undefined,
      });
      setIsAddModalOpen(false);
      setName('');
      setIp('');
      setHostname('');
      setModel('');
      setCheckTypes(['icmp']);
      setSnmpMode('default');
      setShowDeviceDetails(false);
      loadDevices();
      setZabbixRefresh(value => value + 1);
    } catch (err: any) {
      setFormError(err.message || 'Failed to create device');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDeleteDevice = async (id: number, devName: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!window.confirm(`Are you sure you want to delete ${devName}?`)) return;
    try {
      await api.deleteDevice(id);
      if (selectedDevice?.id === id) setSelectedDevice(null);
      loadDevices();
      setZabbixRefresh(value => value + 1);
    } catch (err) {
      setDeleteError((err as Error).message);
    }
  };

  const devicesById = new Map(devices.map(device => [device.id, device]));
  const zabbixLocalIds = new Set(zabbixHosts.flatMap(host => host.local_device_ids));
  const localDevices = devices.filter(device => !zabbixLocalIds.has(device.id));
  const search = searchQuery.trim().toLowerCase();
  const matchesSearch = (fields: (string | undefined | null)[]) =>
    !search || fields.some(field => field?.toLowerCase().includes(search));
  const matchesStatus = (status: string) => filterStatus === 'all' || status.toLowerCase() === filterStatus;
  const filteredDevices = localDevices.filter(device =>
    matchesStatus(device.status) && matchesSearch([device.name, device.ip, device.hostname]));
  const filteredZabbix = zabbixHosts.filter(host => matchesStatus(zabbixAvailability(host).status) && matchesSearch([
    host.name, host.host, ...host.interfaces.flatMap(item => [item.ip, item.dns]),
    ...host.local_device_ids.flatMap(id => {
      const device = devicesById.get(id);
      return device ? [device.name, device.hostname] : [];
    }),
  ]));
  const visibleKeys = [...filteredDevices.map(device => `local-${device.id}`), ...filteredZabbix.map(host => `zabbix-${host.hostid}`)];
  const allSelected = visibleKeys.length > 0 && visibleKeys.every(key => selection.has(key));
  const selectedHosts = zabbixHosts.filter(host => selection.has(`zabbix-${host.hostid}`));
  const selectedIds = [...new Set([...devices.filter(device => selection.has(`local-${device.id}`)).map(device => device.id),
    ...selectedHosts.flatMap(host => host.local_device_ids)])];

  function toggleSelection(keys: string[], checked: boolean) {
    setSelection(previous => {
      const next = new Set(previous);
      for (const key of keys) checked ? next.add(key) : next.delete(key);
      return next;
    });
  }

  async function deleteSelection() {
    setDeleting(true);
    setDeleteError('');
    try {
      await api.deleteDevices(selectedIds, selectedHosts.map(host => host.hostid));
      setDevices(previous => previous.filter(device => !selectedIds.includes(device.id)));
      setZabbixHosts(previous => previous.filter(host => !selection.has(`zabbix-${host.hostid}`)));
      if (selectedDevice && selectedIds.includes(selectedDevice.id)) setSelectedDevice(null);
      setSelection(new Set());
      setConfirmDelete(false);
      refreshInventory();
    } catch (err) {
      setDeleteError((err as Error).message);
    } finally {
      setDeleting(false);
    }
  }

  function refreshInventory() {
    void loadDevices();
    setZabbixRefresh(value => value + 1);
  }

  if (isAdmin && showZabbix) return <section className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h1 className="ops-heading">Zabbix devices</h1>
      <button className="ops-button" onClick={() => setShowZabbix(false)}>Back to monitored devices</button>
    </div>
    <ZabbixDevices devices={devices} onImported={refreshInventory} />
  </section>;

  return (
    <div className="space-y-6">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">
            Monitored Devices
          </h1>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
            ZadDesh devices and hosts visible through the connected Zabbix account.
          </p>
        </div>
        <div className="flex items-center space-x-3">
          {isAdmin && <button className="ops-button" onClick={() => setShowZabbix(true)}>Zabbix devices</button>}
          <button
            aria-label="Refresh devices"
            onClick={refreshInventory}
            className="p-2 rounded-xl border border-slate-200 dark:border-slate-800 text-slate-500 hover:text-slate-900 dark:hover:text-white bg-white dark:bg-slate-900 shadow-sm"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
          {isAdmin && <button
            onClick={() => setIsAddModalOpen(true)}
            className="flex items-center space-x-2 px-4 py-2 bg-sky-700 hover:bg-sky-800 text-white rounded-xl text-sm font-medium shadow-md shadow-sky-600/20 transition-all"
          >
            <Plus className="w-4 h-4" />
            <span>Add Device</span>
          </button>}
        </div>
      </div>

      {/* Filter Tabs */}
      <div className="flex items-center space-x-2 overflow-x-auto pb-1">
        {['all', 'up', 'down', 'warning', 'unknown', 'unmanaged'].map((st) => (
          <button
            key={st}
            onClick={() => setFilterStatus(st)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium capitalize transition-colors ${
              filterStatus === st
                ? 'bg-sky-700 text-white shadow-sm'
                : 'bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800'
            }`}
          >
            {st} ({st === 'all' ? localDevices.length + zabbixHosts.length
              : localDevices.filter(device => device.status === st).length + zabbixHosts.filter(host => zabbixAvailability(host).status === st).length})
          </button>
        ))}
      </div>

      {loadError && <p role="alert" className="ops-notice">{loadError}</p>}
      {deleteError && !confirmDelete && <p role="alert" className="ops-notice">{deleteError}</p>}
      {isAdmin && selection.size > 0 && <div className="ops-notice">
        <strong>{selection.size} selected</strong>
        <button type="button" className="ops-button" onClick={() => { setDeleteError(''); setConfirmDelete(true); }}>
          <Trash2 size={15} aria-hidden="true" /> Delete selected
        </button>
        <button type="button" className="ops-button" onClick={() => setSelection(new Set())}>Clear selection</button>
      </div>}
      {zabbixError && <p role="alert" className="ops-notice">Could not refresh Zabbix inventory. {zabbixError}</p>}
      {zabbixLoading && <p role="status" className="ops-muted text-sm">Checking Zabbix inventory…</p>}
      {!loading && !zabbixLoading && !loadError && !filteredDevices.length && !filteredZabbix.length && <p className="ops-empty">No devices match this view.</p>}
      {/* Devices Table Card */}
      <Card>
        <div className="overflow-x-auto -mx-5 -my-5">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-800 text-[11px] font-mono uppercase text-slate-400">
              <tr>
                {isAdmin && <th scope="col" className="pl-5"><input type="checkbox" aria-label="Select all visible devices"
                  checked={allSelected} disabled={!visibleKeys.length} ref={input => { if (input) input.indeterminate = !allSelected && visibleKeys.some(key => selection.has(key)); }}
                  onChange={event => toggleSelection(visibleKeys, event.target.checked)} /></th>}
                <th className="px-5 py-3.5">Status</th>
                <th className="px-5 py-3.5">Device Name</th>
                <th className="px-5 py-3.5">Target IP / Hostname</th>
                <th className="px-5 py-3.5">Vendor & Model</th>
                <th className="px-5 py-3.5">Uptime</th>
                <th className="px-5 py-3.5">Last Seen</th>
                <th className="px-5 py-3.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-800/80">
              {filteredZabbix.map(host => {
                const label = host.name || host.host;
                const health = zabbixAvailability(host);
                const addresses = [...new Set(host.interfaces.map(item => item.useip === '1' ? item.ip : item.dns).filter(Boolean))];
                const local = host.local_device_ids.length === 1 ? devicesById.get(host.local_device_ids[0]) : undefined;
                return <tr key={`zabbix-${host.hostid}`} className="hover:bg-slate-50 dark:hover:bg-slate-800/40">
                  {isAdmin && <td className="pl-5"><input type="checkbox" aria-label={`Select ${label}`}
                    checked={selection.has(`zabbix-${host.hostid}`)} onChange={event => toggleSelection([`zabbix-${host.hostid}`], event.target.checked)} /></td>}
                  <td className="px-5 py-3.5 whitespace-nowrap"><Badge status={health.status}>{health.label}</Badge></td>
                  <td className="px-5 py-3.5 font-medium">
                    <div className="flex flex-wrap items-center gap-2">
                      <Server className="h-4 w-4 text-sky-500" aria-hidden="true" />
                      <span>{label}</span><Badge status="info">Zabbix</Badge>
                    </div>
                  </td>
                  <td className="px-5 py-3.5 font-mono text-xs ops-muted">{addresses.join(', ') || '—'}</td>
                  <td className="px-5 py-3.5 text-xs ops-muted">—</td>
                  <td className="px-5 py-3.5 text-xs ops-muted">—</td>
                  <td className="px-5 py-3.5 text-xs ops-muted">—</td>
                  <td className="px-5 py-3.5 text-right">
                    <div className="flex flex-wrap items-start justify-end gap-2">
                      {local && <button className="ops-button" aria-label={`Local telemetry for ${label}`} onClick={() => openDeviceDetails(local)}>Local telemetry</button>}
                      <ZabbixAddHostButton host={host} onImported={refreshInventory} />
                    </div>
                  </td>
                </tr>;
              })}
              {!zabbixLoading && filteredDevices.map((dev) => (
                <tr
                  key={dev.id}
                  className="hover:bg-slate-50 dark:hover:bg-slate-800/40 cursor-pointer transition-colors"
                >
                  {isAdmin && <td className="pl-5"><input type="checkbox" aria-label={`Select ${dev.name}`}
                    checked={selection.has(`local-${dev.id}`)} onChange={event => toggleSelection([`local-${dev.id}`], event.target.checked)} /></td>}
                  <td className="px-5 py-3.5 whitespace-nowrap">
                    <Badge status={dev.status} />
                  </td>
                  <td className="px-5 py-3.5 font-medium text-slate-900 dark:text-slate-100 whitespace-nowrap">
                    <div className="flex items-center space-x-2">
                      <Server className="w-4 h-4 text-sky-500" />
                      <button onClick={() => openDeviceDetails(dev)} className="hover:underline">{dev.name}</button>
                    </div>
                  </td>
                  <td className="px-5 py-3.5 font-mono text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap">
                    <div>{dev.ip}</div>
                    {dev.hostname && <div className="text-[10px] text-slate-400">{dev.hostname}</div>}
                  </td>
                  <td className="px-5 py-3.5 text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap">
                    <span>{dev.vendor || 'Generic'}</span>
                    {dev.model && <span className="ml-1 text-slate-400">({dev.model})</span>}
                  </td>
                  <td className="px-5 py-3.5 font-mono text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap">
                    {dev.uptime_seconds > 0
                      ? `${Math.floor(dev.uptime_seconds / 86400)}d ${Math.floor(
                          (dev.uptime_seconds % 86400) / 3600
                        )}h`
                      : '—'}
                  </td>
                  <td className="px-5 py-3.5 text-xs text-slate-400 whitespace-nowrap">
                    {dev.last_seen ? new Date(dev.last_seen).toLocaleTimeString() : 'Never'}
                  </td>
                  <td className="px-5 py-3.5 text-right whitespace-nowrap">
                    {isAdmin && <button
                      aria-label={`Delete ${dev.name}`}
                      onClick={(e) => handleDeleteDevice(dev.id, dev.name, e)}
                      className="p-1.5 rounded-lg text-slate-400 hover:text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950/40 transition-colors"
                      title="Delete Device"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Modal title="Delete selected devices" isOpen={confirmDelete} onClose={() => { if (!deleting) setConfirmDelete(false); }}>
        <div className="space-y-3 text-sm">
          {selectedIds.length > 0 && <p>This permanently deletes {selectedIds.length} ZadDesh {selectedIds.length === 1 ? 'device' : 'devices'} and associated checks and history.</p>}
          {selectedHosts.length > 0 && <p>Selected Zabbix hosts will be hidden in ZadDesh. You can add them again from Zabbix devices.</p>}
          {deleteError && <p role="alert" className="ops-notice">{deleteError}</p>}
          <div className="flex justify-end gap-2">
            <button type="button" className="ops-button" disabled={deleting} onClick={() => setConfirmDelete(false)}>Cancel</button>
            <button type="button" disabled={deleting || (!selectedIds.length && !selectedHosts.length)} onClick={() => void deleteSelection()}
              className="rounded-lg bg-rose-700 px-3 py-2 font-semibold text-white hover:bg-rose-800 disabled:opacity-50">
              {deleting ? 'Deleting…' : 'Confirm deletion'}
            </button>
          </div>
        </div>
      </Modal>

      {/* Device Telemetry & Detail Modal */}
      {selectedDevice && (
        <Modal
          wide
          isOpen={!!selectedDevice}
          onClose={() => setSelectedDevice(null)}
          title={`Telemetry: ${selectedDevice.name} (${selectedDevice.ip})`}
        >
          <div className="space-y-6">
            {/* Meta header */}
            <div className="grid grid-cols-3 gap-3 p-3 bg-slate-50 dark:bg-slate-800/50 rounded-xl border border-slate-200 dark:border-slate-800 text-xs">
              <div>
                <span className="text-slate-400 block text-[10px] uppercase font-mono">Status</span>
                <Badge status={selectedDevice.status} />
              </div>
              <div>
                <span className="text-slate-400 block text-[10px] uppercase font-mono">Platform</span>
                <span className="font-semibold text-slate-800 dark:text-slate-200">
                  {selectedDevice.os || selectedDevice.vendor || 'Unknown'}
                </span>
              </div>
              <div>
                <span className="text-slate-400 block text-[10px] uppercase font-mono">Last Seen</span>
                <span className="font-mono text-slate-800 dark:text-slate-200">
                  {selectedDevice.last_seen
                    ? new Date(selectedDevice.last_seen).toLocaleTimeString()
                    : 'Never'}
                </span>
              </div>
            </div>

            <div className="flex items-center justify-between gap-3">
              <label htmlFor="metric-range" className="text-sm font-semibold">Time range</label>
              <select id="metric-range" className="ops-button" value={range} onChange={event => { setMetricsLoading(true); setRange(Number(event.target.value)); }}>
                <option value={5}>Last 5 minutes</option><option value={60}>Last hour</option><option value={120}>Last 2 hours</option><option value={1440}>Last 24 hours</option>
              </select>
            </div>
            {telemetryError && <p role="alert" className="ops-notice">{telemetryError}</p>}
            {metricsLoading ? (
              <div className="py-12 text-center text-xs text-slate-400 animate-pulse font-mono">
                Loading time-series telemetry charts...
              </div>
            ) : (
              <div className="space-y-5">
                {/* ICMP Latency Chart */}
                <div className="p-4 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl shadow-sm">
                  <MetricChart
                    title="ICMP Round-Trip Latency"
                    unit="ms"
                    intervalSeconds={deviceChecks.find(check => check.check_type === "icmp")?.interval_seconds}
                    data={rttMetrics}
                    color="#0284c7"
                  />
                </div>

                {/* Availability History Chart */}
                <div className="p-4 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl shadow-sm">
                  <MetricChart
                    title="ICMP Poll Availability"
                    unit="%"
                    intervalSeconds={deviceChecks.find(check => check.check_type === "icmp")?.interval_seconds}
                    data={availMetrics}
                    color="#10b981"
                  />
                </div>

                {(httpMetrics.length > 0 || deviceChecks.some(check => ["http", "https"].includes(check.check_type))) && <MetricChart title="HTTP Response Time" unit="ms" data={httpMetrics} intervalSeconds={deviceChecks.find(check => ["http", "https"].includes(check.check_type))?.interval_seconds} />}
                {(tcpMetrics.length > 0 || deviceChecks.some(check => check.check_type === "tcp")) && <MetricChart title="TCP Connect Time" unit="ms" data={tcpMetrics} intervalSeconds={deviceChecks.find(check => check.check_type === "tcp")?.interval_seconds} />}
                {snmpMetrics.map(item => <MetricChart key={item.key} title={item.key} unit={item.unit} data={item.points} />)}
                {deviceChecks.some(check => check.check_type === 'snmp') && snmpMetrics.length === 0 && <p className="ops-empty">No numeric SNMP samples have arrived for this device.</p>}
                {/* CPU Utilization Chart */}
                {cpuMetrics.length > 0 && (
                  <div className="p-4 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl shadow-sm">
                    <MetricChart
                      title="CPU Utilization"
                      unit="%"
                      data={cpuMetrics}
                      color="#f59e0b"
                    />
                  </div>
                )}
              </div>
            )}

            {/* Configured Checks list */}
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2 font-mono">
                Assigned Probes ({deviceChecks.length})
              </h4>
              <div className="divide-y divide-slate-100 dark:divide-slate-800 border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden">
                {deviceChecks.map((chk) => (
                  <div key={chk.id} className="p-3 flex items-center justify-between text-xs">
                    <div>
                      <div className="font-medium text-slate-900 dark:text-slate-100">
                        {chk.name}
                      </div>
                      <div className="font-mono text-[10px] text-slate-400">
                        {chk.check_type.toUpperCase()} • {chk.target} • every {chk.interval_seconds}s
                      </div>
                    </div>
                    <Badge status={chk.state?.current_status || 'unknown'} />
                  </div>
                ))}
              </div>
            </div>
          </div>
        </Modal>
      )}

      {/* Add Device Modal Wizard */}
      <Modal
        isOpen={isAddModalOpen}
        onClose={() => setIsAddModalOpen(false)}
        title="Add Monitored Device"
        wide
      >
        <form onSubmit={handleCreateDevice} className="space-y-4">
          {formError && (
            <div className="p-3 text-xs bg-rose-500/10 border border-rose-500/30 text-rose-600 dark:text-rose-400 rounded-xl">
              {formError}
            </div>
          )}

          <div hidden={hideDeviceDetails}>
            <label htmlFor="device-device" className="block text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-1">
              Device Name {defaultSnmp ? '(optional)' : '*'}
            </label>
            <input id="device-device"
              type="text"
              required={!defaultSnmp}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. core-switch-01"
              className="w-full px-3.5 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm outline-none focus:border-sky-500"
            />
          </div>

          <div className={hideDeviceDetails ? '' : 'grid grid-cols-2 gap-3'}>
            <div>
              <label htmlFor="device-ip" className="block text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-1">
                IP Address *
              </label>
              <input id="device-ip"
                type="text"
                required
                value={ip}
                onChange={(e) => setIp(e.target.value)}
                placeholder="192.168.1.1"
                className="w-full px-3.5 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm outline-none focus:border-sky-500"
              />
            </div>
            <div hidden={hideDeviceDetails}>
              <label htmlFor="device-hostname" className="block text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-1">
                Hostname / FQDN
              </label>
              <input id="device-hostname"
                type="text"
                value={hostname}
                onChange={(e) => setHostname(e.target.value)}
                placeholder="switch.corp.internal"
                className="w-full px-3.5 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm outline-none focus:border-sky-500"
              />
            </div>
          </div>

          {defaultSnmp && <button type="button" className="text-sm underline" aria-expanded={showDeviceDetails}
            onClick={() => setShowDeviceDetails(!showDeviceDetails)}>
            {showDeviceDetails ? 'Hide device details' : 'Show optional device details'}
          </button>}

          <div className={hideDeviceDetails ? 'hidden' : 'grid grid-cols-2 gap-3'}>
            <div>
              <label htmlFor="device-vendor" className="block text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-1">
                Vendor
              </label>
              <input id="device-vendor" list="device-vendors"
                value={vendor}
                onChange={(e) => setVendor(e.target.value)}
                className="w-full px-3.5 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm outline-none focus:border-sky-500"
              />
              <datalist id="device-vendors">
                {['Generic', 'Cisco', 'Juniper', 'MikroTik', 'Dell', 'Linux', 'Windows', 'Ubiquiti'].map(option => <option key={option} value={option} />)}
              </datalist>
            </div>
            <div>
              <label htmlFor="device-site" className="block text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-1">
                Site / Location
              </label>
              <select id="device-site"
                value={siteId || ''}
                onChange={(e) => setSiteId(e.target.value ? Number(e.target.value) : undefined)}
                className="w-full px-3.5 py-2 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm outline-none focus:border-sky-500"
              >
                <option value="">Default Site</option>
                {sites.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-2">
              Initial Check Presets
            </label>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
              {[
                { id: 'icmp', label: 'ICMP Ping' },
                { id: 'http', label: 'HTTP' },
                { id: 'https', label: 'HTTPS' },
                { id: 'tcp', label: 'TCP Port' },
                { id: 'snmp', label: 'SNMP' },
              ].map((chk) => {
                const checked = checkTypes.includes(chk.id);
                return (
                  <label
                    key={chk.id}
                    className={`flex items-center space-x-2 p-2.5 rounded-xl border text-xs font-medium cursor-pointer transition-all ${
                      checked
                        ? 'border-sky-500 bg-sky-500/10 text-sky-600 dark:text-sky-400 font-semibold'
                        : 'border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-400'
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(e) => {
                        if (e.target.checked) setCheckTypes([...checkTypes, chk.id]);
                        else setCheckTypes(checkTypes.filter((x) => x !== chk.id));
                      }}
                      className="sr-only"
                    />
                    <span>{chk.label}</span>
                  </label>
                );
              })}
            </div>
          </div>

          {checkTypes.includes('tcp') && <label className="block text-sm">
            TCP port
            <input name="tcp-port" type="number" min={1} max={65535} defaultValue={80} required className="dash-select ml-2" />
          </label>}
          {isAddModalOpen && checkTypes.includes('snmp') && (
            <fieldset className="min-w-0 rounded-xl border border-slate-200 p-4 dark:border-slate-700">
              <legend className="px-2 text-sm font-semibold">SNMP interface</legend>
              <label className="mb-4 block text-sm">SNMP settings
                <select className="dash-select mt-1 block w-full" value={snmpMode} onChange={event => setSnmpMode(event.target.value)}>
                  <option value="default">Default SNMP</option>
                  <option value="custom">Custom SNMP settings</option>
                </select>
              </label>
              {defaultSnmp ? <DefaultSnmpFields ip={ip} /> : <SnmpFields deviceInterface={{ ip, hostname }} />}
            </fieldset>
          )}

          <div className="pt-2 flex justify-end space-x-3">
            <button
              type="button"
              onClick={() => setIsAddModalOpen(false)}
              className="px-4 py-2 rounded-xl text-sm text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="px-5 py-2 bg-sky-700 hover:bg-sky-800 text-white rounded-xl text-sm font-medium shadow-md shadow-sky-600/20 transition-all disabled:opacity-50"
            >
              {isSubmitting ? 'Creating...' : 'Provision Device'}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
};
