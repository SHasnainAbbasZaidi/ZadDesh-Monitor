import React, { lazy, Suspense, useState, useEffect } from 'react';
import { useAuth } from './context/AuthContext';
import { AppLayout } from './components/layout/AppLayout';
import { Login } from './pages/Login';
import { Overview } from './pages/Overview';
import { Devices } from './pages/Devices';
import { Checks } from './pages/Checks';
import { Alerts } from './pages/Alerts';
import { Settings } from './pages/Settings';
import { Logs } from './pages/Logs';
import { api, request } from './services/api';
import { IncidentWorkspace } from './pages/IncidentWorkspace';
import type { IncidentPreview, IncidentPreviewSummary } from './types';

const NetworkDiagram = lazy(() => import('./pages/NetworkDiagram'));
const EmailAlerts = lazy(() => import('./pages/EmailAlerts'));
const NetworkDiagramWidget = lazy(() => import('./pages/NetworkDiagram').then(module => ({ default: module.NetworkDiagramWidget })));

export const AppContent: React.FC = () => {
  const { user, isLoading, isSetupRequired } = useAuth();
  const [currentTab, setCurrentTab] = useState(() => window.location.hash === '#email-alerts' ? 'email-alerts' : 'overview');
  const [localIncidents, setLocalIncidents] = useState<IncidentPreviewSummary>();
  const [zabbixIncidents, setZabbixIncidents] = useState<IncidentPreviewSummary>();
  const [incidentRefresh, setIncidentRefresh] = useState(0);
  const [demoMode, setDemoMode] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [features, setFeatures] = useState<{ incident_workspace: boolean; network_diagram_beta: boolean }>();
  const incidentWorkspace = features?.incident_workspace;

  useEffect(() => {
    const onHashChange = () => setCurrentTab(previous => window.location.hash === '#email-alerts' ? 'email-alerts' : previous === 'email-alerts' ? 'overview' : previous);
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  useEffect(() => {
    if (!user || isLoading) return;
    if (currentTab === 'email-alerts' && user.role === 'admin') {
      if (window.location.hash !== '#email-alerts') window.history.replaceState(null, '', '#email-alerts');
    } else if (window.location.hash === '#email-alerts') {
      window.history.replaceState(null, '', window.location.pathname + window.location.search);
    }
  }, [currentTab, user, isLoading]);

  useEffect(() => {
    if (!user) return;
    api.getSystemInfo().then(info => setDemoMode(info.environment === "demo")).catch(() => {});
    request<NonNullable<typeof features>>('/features').then(setFeatures)
      .catch(() => setFeatures({ incident_workspace: false, network_diagram_beta: false }));
  }, [user]);

  useEffect(() => {
    if (!user || incidentWorkspace === undefined) return;
    let active = true;
    const fetchIncidents = () => {
      const preview = incidentWorkspace
        ? request<{ total: number; alerts: (Omit<IncidentPreview, 'id' | 'timestamp'> & { id: number; started_at: string })[] }>(
          '/incidents/alerts?view=current&category=critical&limit=8&include_count=true')
          .then(result => ({ total: result.total, entries: result.alerts.map(row => ({
            ...row, id: `local-${row.id}`, timestamp: row.started_at,
          })) }))
        : api.getAlerts('active').then(alerts => ({ total: alerts.length, entries: alerts.slice(0, 8).map(row => ({
          ...row, id: `local-${row.id}`, timestamp: row.triggered_at,
        })) }));
      void preview.then(result => { if (active) setLocalIncidents(result); }).catch(() => {
        if (active) setLocalIncidents(previous => previous && { ...previous, incomplete: true });
      });
    };
    fetchIncidents();
    const interval = setInterval(fetchIncidents, 15000);
    return () => { active = false; clearInterval(interval); };
  }, [user, incidentWorkspace, incidentRefresh]);

  useEffect(() => {
    if (!user || !incidentWorkspace) return;
    const controller = new AbortController();
    function refresh() {
      void request<{ total: number; entries: IncidentPreview[]; scan_limited: boolean }>(
        '/incidents/zabbix?category=critical', { signal: controller.signal })
        .then(result => { if (!controller.signal.aborted) setZabbixIncidents({
          total: result.total, entries: result.entries.map(row => ({ ...row, id: `zabbix-${row.id}` })),
          incomplete: result.scan_limited,
        }); })
        .catch(() => { if (!controller.signal.aborted) setZabbixIncidents(previous => ({
          total: previous?.total ?? 0, entries: previous?.entries ?? [], incomplete: true,
        })); });
    }
    refresh();
    const timer = window.setInterval(refresh, 60_000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [user, incidentWorkspace, incidentRefresh]);

  if (isLoading) {
    return (
      <div className="h-screen w-screen flex flex-col items-center justify-center bg-slate-950 text-slate-100 space-y-4">
        <div className="w-12 h-12 rounded-2xl bg-gradient-to-tr from-sky-600 to-cyan-400 flex items-center justify-center font-black text-2xl animate-pulse">
          Z
        </div>
        <p className="text-xs font-mono tracking-widest text-slate-400 uppercase">
          Loading ZadDesh Core...
        </p>
      </div>
    );
  }

  if (!user || isSetupRequired) {
    return <Login />;
  }

  const admin = user.role === 'admin';
  const tab = admin || ['overview', 'logs', 'alerts'].includes(currentTab) ? currentTab : 'overview';
  const remote = incidentWorkspace ? zabbixIncidents : undefined;
  const incidents = localIncidents && {
    total: localIncidents.total + (remote?.total ?? 0),
    entries: [...localIncidents.entries, ...(remote?.entries ?? [])].sort((a, b) => b.timestamp.localeCompare(a.timestamp)).slice(0, 8),
    incomplete: localIncidents.incomplete || (incidentWorkspace && (!remote || remote.incomplete)),
  };
  return (
    <AppLayout
      currentTab={tab}
      onSelectTab={(tab) => {
        setCurrentTab(tab);
        setSearchQuery('');
      }}
      activeAlertsCount={incidents?.total ?? 0}
      networkDiagram={features?.network_diagram_beta}
      onSearchChange={admin ? (query) => {
        setSearchQuery(query);
        if (query && currentTab !== 'devices') {
          setCurrentTab('devices');
        }
      } : undefined}
    >
      {demoMode && <div className="ops-notice" role="status"><strong>Demo workspace</strong> Sample devices, metrics, and incidents. Collection is disabled.</div>}
      {tab === 'overview' && (
        <Overview
          incidents={incidents}
          criticalOnly={incidentWorkspace === true}
          onRefreshIncidents={() => setIncidentRefresh(value => value + 1)}
          onNavigateToDevices={admin ? () => setCurrentTab('devices') : undefined}
          onNavigateToAlerts={() => setCurrentTab('alerts')}
        />
      )}
      {tab === 'overview' && features?.network_diagram_beta && <Suspense><NetworkDiagramWidget /></Suspense>}
      {tab === 'network-diagram' && admin && features?.network_diagram_beta && <Suspense><NetworkDiagram /></Suspense>}
      {tab === 'devices' && <Devices searchQuery={searchQuery} />}
      {tab === 'checks' && <Checks />}
      {tab === 'alerts' && (incidentWorkspace ? <IncidentWorkspace /> : <Alerts />)}
      {tab === 'email-alerts' && admin && <Suspense fallback={<p role="status">Loading email notifications…</p>}><EmailAlerts /></Suspense>}
      {tab === 'logs' && (incidentWorkspace ? <IncidentWorkspace initialTab="logs" /> : <Logs />)}
      {tab === 'settings' && <Settings />}
    </AppLayout>
  );
};

