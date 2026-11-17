import { ArrowRight } from 'lucide-react';
import { RollingCount } from './RollingCount';
import type { DeviceStats, FleetOverview, IncidentPreview, IncidentPreviewSummary } from '../../types';
import './overviewSummary.css';

const statusGroups = [
  { key: 'up', label: 'Healthy', color: 'var(--ops-up)' },
  { key: 'warning', label: 'Degraded', color: 'var(--ops-warning)' },
  { key: 'down', label: 'Down', color: 'var(--ops-down)' },
  { key: 'unknown', label: 'No availability data', color: 'var(--ops-unknown)' },
  { key: 'unmanaged', label: 'Unmanaged', color: '#a788d9' },
] as const;

const incidentGroups: Record<NonNullable<IncidentPreview['category']>, { label: string; color: string }> = {
  device_offline: { label: 'Device offline', color: 'var(--ops-down)' },
  link_down: { label: 'Link down', color: '#f18b43' },
  cpu_usage: { label: 'CPU usage', color: '#7eaff0' },
  memory_usage: { label: 'Memory usage', color: '#a788d9' },
  network_usage: { label: 'Network usage', color: '#388eae' },
  storage_shortage: { label: 'Storage shortage', color: 'var(--ops-warning)' },
  other: { label: 'Other', color: 'var(--ops-unknown)' },
};

type Slice = { label: string; color: string; count: number };

export function OverviewTiles({ stats, incidents, incidentLabel, fleetFailed, onDevices, onAlerts }: {
  stats?: DeviceStats; incidents?: IncidentPreviewSummary; incidentLabel: string; fleetFailed: boolean;
  onDevices?: () => void; onAlerts: () => void;
}) {
  const tiles = [
    { label: 'Total devices', value: stats?.total_devices, color: 'blue', detail: 'Combined inventory', action: onDevices },
    { label: 'Healthy', value: stats?.up, color: 'green', detail: 'Available devices', action: onDevices },
    { label: 'Needs attention', value: stats && stats.down + stats.warning, color: 'amber', detail: 'Down or degraded', action: onDevices },
    { label: incidentLabel, value: incidents?.total, color: 'pink', detail: incidents?.incomplete ? 'Partial incident data' : 'Current alert feed', action: onAlerts },
    { label: 'Down', value: stats?.down, color: 'rose', detail: 'Unavailable devices', action: onDevices },
    { label: 'Degraded', value: stats?.warning, color: 'yellow', detail: 'Devices with warnings', action: onDevices },
    { label: 'No availability data', value: stats?.unknown, color: 'mint', detail: 'Status unknown', action: onDevices },
    { label: 'Unmanaged', value: stats?.unmanaged, color: 'lavender', detail: 'Devices outside monitoring', action: onDevices },
  ];
  return <section className="overview-tiles" aria-label="Infrastructure summary" aria-busy={!stats && !fleetFailed}>
    {tiles.map(({ label, value, color, detail, action }) => {
      const Item = action ? 'button' : 'div';
      return <Item key={label} type={action ? 'button' : undefined} className="overview-tile" data-color={color} onClick={action}>
        <span className="overview-tile-label">{label}</span>
        <RollingCount value={value} />
        <span className="overview-tile-detail">{detail}{action && <ArrowRight size={13} aria-hidden="true" />}</span>
      </Item>;
    })}
  </section>;
}

function Donut({ id, title, summary, slices, unit }: { id: string; title: string; summary: string; slices?: Slice[]; unit: string }) {
  const total = slices?.reduce((sum, slice) => sum + slice.count, 0);
  let offset = 0;
  const stops = total ? slices?.filter(slice => slice.count > 0).map(slice => {
    const start = offset;
    offset += slice.count;
    return `${slice.color} ${start / total * 100}% ${offset / total * 100}%`;
  }) : undefined;
  const fill = stops?.length === 1 ? slices?.find(slice => slice.count > 0)?.color
    : stops?.length ? `conic-gradient(${stops.join(', ')})` : undefined;
  return <section className="dash-panel overview-donut-panel" aria-labelledby={id}>
    <header className="overview-donut-heading"><h2 id={id}>{title}</h2><p>{summary}</p></header>
    <div className="overview-donut" style={{ background: fill }}>
      <div className="overview-donut-center"><strong>{total?.toLocaleString() ?? '—'}</strong><span>{unit}</span></div>
    </div>
    {slices && <dl className="overview-donut-legend">{slices.map(slice => <div key={slice.label}>
      <dt><i style={{ background: slice.color }} aria-hidden="true" />{slice.label}</dt><dd>{slice.count.toLocaleString()}</dd>
    </div>)}</dl>}
  </section>;
}

export function OverviewDonuts({ fleet, fleetFailed, incidents }: {
  fleet?: FleetOverview; fleetFailed: boolean; incidents?: IncidentPreviewSummary;
}) {
  const stats = fleet?.stats;
  const status = stats && statusGroups.map(group => ({ ...group, count: stats[group.key] }));
  const sources = fleet && [
    { label: 'Zabbix', color: '#419dbf', count: fleet.devices.filter(device => device.source === 'zabbix').length },
    { label: 'ZadDesh', color: '#ec9452', count: fleet.devices.filter(device => device.source === 'zaddesh').length },
  ];
  const byCategory = new Map<NonNullable<IncidentPreview['category']>, number>();
  for (const incident of incidents?.entries ?? []) {
    const category = incident.category ?? 'other';
    byCategory.set(category, (byCategory.get(category) ?? 0) + 1);
  }
  const recent = incidents && Array.from(byCategory, ([category, count]) => ({ ...(incidentGroups[category] ?? incidentGroups.other), count }));
  const share = stats?.total_devices ? new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 1 }).format(stats.up / stats.total_devices) : undefined;
  const fleetSummary = fleetFailed ? fleet ? (fleet.zabbix_unavailable ? 'Zabbix unavailable · Local devices' : 'Last reported fleet status') : 'Fleet status unavailable'
    : share ? `${share} healthy` : fleet ? 'No devices configured' : 'Waiting for the monitoring API';
  const sourceSummary = fleetFailed ? fleet ? 'Last available inventory' : 'Inventory unavailable' : 'Combined monitoring inventory';
  const incidentSummary = incidents?.incomplete ? 'Partial incident data' : incidents ? `Showing ${incidents.entries.length} of ${incidents.total} current incidents` : 'Waiting for alert data';
  return <div className="overview-donuts" aria-label="Monitoring breakdowns">
    <Donut id="fleet-title" title="Fleet health" summary={fleetSummary} slices={status} unit="devices" />
    <Donut id="device-sources-title" title="Devices by source" summary={sourceSummary} slices={sources} unit="devices" />
    <Donut id="incident-breakdown-title" title="Recent incidents by type" summary={incidentSummary} slices={recent} unit="shown" />
  </div>;
}
