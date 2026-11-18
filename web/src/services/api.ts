import { User, Device, DeviceStats, Site, Group, Check, Alert, SystemEvent, SystemInfo } from '../types';

const BASE_URL = '/api/v1';

export const logSeverities = ['emergency', 'alert', 'critical', 'error', 'warning', 'notice', 'info', 'debug'] as const;
export interface InfrastructureLog {
  event_id: string;
  timestamp: string;
  source: string;
  severity: typeof logSeverities[number];
  message: string;
  origin?: 'zaddesh' | 'integration';
  provider?: string;
  device_id?: number;
}

export async function request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem('zaddesh_token');
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
  };

  if (token) {
    (headers as Record<string, string>)['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${BASE_URL}${endpoint}`, {
    ...options,
    headers,
  });

  if (response.status === 401 && !endpoint.includes('/auth/login') && !endpoint.includes('/auth/setup')) {
    localStorage.removeItem('zaddesh_token');
    window.dispatchEvent(new Event('zaddesh:session-expired'));
  }

  if (!response.ok) {
    let errorMsg = `HTTP Error ${response.status}`;
    try {
      const errJson = await response.json();
      if (Array.isArray(errJson.detail)) {
        errorMsg = errJson.detail.map((item: { msg: string }) => item.msg).join('; ') || errorMsg;
      } else {
        errorMsg = errJson.detail || errJson.message || errorMsg;
      }
    } catch {
      // Use fallback errorMsg
    }
    throw new Error(errorMsg);
  }

  if (response.status === 204) {
    return {} as T;
  }

  return response.json();
}

export const api = {
  getLogs: (filters: { source?: string; severity?: string; origin?: string; provider?: string; device_id?: number }, signal: AbortSignal) => {
    const query = new URLSearchParams();
    if (filters.source) query.set('source', filters.source);
    if (filters.severity) query.set('severity', filters.severity);
    if (filters.origin) query.set('origin', filters.origin);
    if (filters.provider) query.set('provider', filters.provider);
    if (filters.device_id) query.set('device_id', String(filters.device_id));
    return request<InfrastructureLog[]>(`/logs?${query}`, { signal });
  },
  // Auth
  getAuthStatus: () => request<{ setup_required: boolean; allow_registration: boolean }>('/auth/status'),
  setupAdmin: (data: any) => request<{ access_token: string; user: User }>('/auth/setup', {
    method: 'POST',
    body: JSON.stringify(data),
  }),
  login: (data: any) => request<{ access_token: string; user: User }>('/auth/login', {
    method: 'POST',
    body: JSON.stringify(data),
  }),
  getProfile: () => request<User>('/auth/me'),
  getUsers: () => request<User[]>('/auth/users'),
  createUser: (data: { username: string; email: string; full_name?: string; role: User['role']; password: string }) =>
    request<User>('/auth/users', { method: 'POST', body: JSON.stringify(data) }),
  updateUser: (id: number, data: { role?: User['role']; is_active?: boolean; password?: string; current_password?: string }) =>
    request<User>(`/auth/users/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),

  // Devices & Infrastructure
  getDeviceStats: () => request<DeviceStats>('/devices/stats'),
  getDevices: async (params?: { query?: string; status?: string; site_id?: number; snmp_only?: boolean }) => {
    const q = new URLSearchParams();
    if (params?.query) q.set('query', params.query);
    if (params?.snmp_only) q.set('snmp_only', 'true');
    if (params?.status) q.set('status', params.status);
    if (params?.site_id) q.set('site_id', params.site_id.toString());
    q.set('limit', '1000');
    const devices: Device[] = [];
    let page: Device[];
    do {
      q.set('offset', String(devices.length));
      page = await request<Device[]>(`/devices?${q}`);
      devices.push(...page);
    } while (page.length === 1000);
    return devices;
  },
  getDevice: (id: number) => request<Device & { site?: Site; group?: Group }>(`/devices/${id}`),
  createDevice: (data: any) => request<Device>('/devices', {
    method: 'POST',
    body: JSON.stringify(data),
  }),
  updateDeviceMonitoring: (id: number, changes: Partial<Pick<Device, 'is_priority' | 'collection_mode'>>) => request<Device>(`/devices/${id}`, { method: 'PUT', body: JSON.stringify(changes) }),
  deleteDevice: (id: number) => request<void>(`/devices/${id}`, { method: 'DELETE' }),
  deleteDevices: (device_ids: number[], zabbix_host_ids: string[]) => request<{ deleted: number; zabbix_hidden: number }>(
    '/devices/bulk-delete', { method: 'POST', body: JSON.stringify({ device_ids, zabbix_host_ids }) }),

  // Sites & Groups
  getSites: () => request<Site[]>('/sites'),
  getGroups: () => request<Group[]>('/groups'),

  // Checks
  createSnmpCheck: (data: {
    device_id: number; name: string; target: string; interval_seconds: number;
    params: {
      credential: string; version: string; operation: string; oid: string; port: number;
      security_level?: string; auth_protocol?: string; privacy_protocol?: string;
    };
  }) => request<Check>('/checks', {
    method: 'POST',
    body: JSON.stringify({ ...data, check_type: 'snmp' }),
  }),
  getChecks: (params?: { device_id?: number; check_type?: string; is_enabled?: boolean }, signal?: AbortSignal) => {
    const q = new URLSearchParams();
    if (params?.device_id) q.set('device_id', params.device_id.toString());
    if (params?.check_type) q.set('check_type', params.check_type);
    if (params?.is_enabled !== undefined) q.set('is_enabled', String(params.is_enabled));
    const queryStr = q.toString() ? `?${q.toString()}` : '';
    return request<Check[]>(`/checks${queryStr}`, { signal });
  },

  // Metrics
  getLatestMetrics: (deviceId: number) => request<{ metric_key: string; value: number; tags?: Record<string, string> }[]>(`/metrics/latest?device_id=${deviceId}`),
  getProbeLatency: (protocol: string, minutes: number, deviceId: string, signal: AbortSignal, priorityOnly = false) => {
    const query = new URLSearchParams({ protocol, minutes: String(minutes) });
    if (deviceId !== 'all') query.set('device_id', deviceId);
    if (priorityOnly) query.set('priority_only', 'true');
    return request<{
      devices: Array<{ id: number; name: string; ip: string; status: string }>;
      channels: Array<{
        device_id: number; check_name: string; probe_down: boolean; interval_seconds: number;
        latency: Array<{ timestamp: string; value: number }>; failures: string[];
      }>;
      start: string;
      end: string;
      bucket_seconds: number;
    }>(`/metrics/probe-latency?${query}`, { signal });
  },
  queryMetrics: (data: { device_id: number; check_id?: number; metric_keys: string[]; start_time: string; end_time: string }, signal?: AbortSignal) =>
    request<{ series: Array<{ metric_key: string; device_id: number; points: Array<{ timestamp: string; value: number }> }> }>('/metrics/query', {
      method: 'POST',
      body: JSON.stringify(data),
      signal,
    }),

  // Alerts & Events
  getAlerts: (status?: string) => {
    const q = status ? `?status=${status}` : '';
    return request<Alert[]>(`/alerts${q}`);
  },
  acknowledgeAlert: (alertId: number, comment: string) =>
    request<Alert>(`/alerts/${alertId}/acknowledge`, {
      method: 'POST',
      body: JSON.stringify({ comment }),
    }),
  resolveAlert: (alertId: number) =>
    request<Alert>(`/alerts/${alertId}/resolve`, {
      method: 'POST',
    }),
  getEvents: (limit: number = 30) => request<SystemEvent[]>(`/events?limit=${limit}`),

  // System
  getSystemInfo: () => request<SystemInfo>('/system/info'),
};

