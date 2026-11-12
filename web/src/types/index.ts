export interface User {
  id: number;
  username: string;
  email: string;
  full_name?: string;
  role: 'admin' | 'viewer';
  is_active: boolean;
  created_at: string;
}

export interface Site {
  id: number;
  name: string;
  slug: string;
  description?: string;
  address?: string;
  latitude?: string;
  longitude?: string;
}

export interface Group {
  id: number;
  name: string;
  slug: string;
  parent_id?: number;
  description?: string;
  tags: string[];
}

export interface Device {
  id: number;
  name: string;
  ip: string;
  hostname?: string;
  site_id?: number;
  group_id?: number;
  status: 'up' | 'down' | 'warning' | 'unknown' | 'unmanaged';
  os?: string;
  vendor?: string;
  model?: string;
  uptime_seconds: number;
  last_seen?: string;
  is_monitored: boolean;
  created_at: string;
  updated_at: string;
}

export interface DeviceStats {
  total_devices: number;
  up: number;
  down: number;
  warning: number;
  unknown: number;
  unmanaged: number;
}

export interface FleetOverview {
  stats: DeviceStats;
  devices: { id: string; name: string; ip: string; site_id: number | null; status: Device['status']; status_label?: string; source: 'zaddesh' | 'zabbix' }[];
  zabbix_unavailable?: boolean;
}

export interface IncidentPreview {
  id: string;
  title: string;
  severity: string;
  timestamp: string;
  category?: 'device_offline' | 'link_down' | 'other';
  device?: string;
  message?: string;
}

export interface IncidentPreviewSummary {
  total: number;
  entries: IncidentPreview[];
  incomplete?: boolean;
}

export interface Check {
  id: number;
  device_id: number;
  template_id?: number;
  name: string;
  check_type: 'icmp' | 'http' | 'https' | 'tcp' | 'udp' | 'snmp';
  target: string;
  params: Record<string, any>;
  interval_seconds: number;
  timeout_seconds: number;
  retries: number;
  is_enabled: boolean;
  created_at: string;
  state?: {
    current_status: 'ok' | 'warning' | 'critical' | 'unknown';
    last_check_time?: string;
    last_change_time?: string;
    consecutive_failures: number;
    flap_detected: boolean;
    message?: string;
  };
}

export interface Alert {
  id: number;
  rule_id?: number;
  device_id: number;
  check_id?: number;
  severity: 'info' | 'warning' | 'average' | 'high' | 'critical';
  status: 'active' | 'acknowledged' | 'resolved';
  title: string;
  message?: string;
  value?: number;
  triggered_at: string;
  resolved_at?: string;
}

export interface SystemEvent {
  id: number;
  device_id?: number;
  check_id?: number;
  event_type: string;
  severity: string;
  title: string;
  message?: string;
  details: Record<string, any>;
  timestamp: string;
}

export interface SystemInfo {
  version: string;
  status: string;
  environment: string;
  is_setup_required: boolean;
  database_type: string;
  uptime_seconds: number;
  counts: {
    users: number;
    devices: number;
    checks: number;
    active_alerts: number;
    collectors: number;
  };
}
