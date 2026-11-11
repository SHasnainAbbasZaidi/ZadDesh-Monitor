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

const NetworkDiagram = lazy(() => import('./pages/NetworkDiagram'));
const NetworkDiagramWidget = lazy(() => import('./pages/NetworkDiagram').then(module => ({ default: module.NetworkDiagramWidget })));

export const AppContent: React.FC = () => {
  const { user, isLoading, isSetupRequired } = useAuth();
  const [currentTab, setCurrentTab] = useState('overview');
  const [activeAlertsCount, setActiveAlertsCount] = useState(0);
  const [demoMode, setDemoMode] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [features, setFeatures] = useState({ incident_workspace: false, network_diagram_beta: false });

  // Fetch active alerts count periodically for the sidebar badge
  useEffect(() => {
    if (!user) return;
    const fetchBadge = () => {
      api.getAlerts('active')
        .then((alerts) => setActiveAlertsCount(alerts.length))
        .catch(() => {});
    };
    fetchBadge();
    api.getSystemInfo().then(info => setDemoMode(info.environment === "demo")).catch(() => {});
    request<typeof features>('/features').then(setFeatures).catch(() => {});
    const interval = setInterval(fetchBadge, 15000);
    return () => clearInterval(interval);
  }, [user]);

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
  return (
    <AppLayout
      currentTab={tab}
      onSelectTab={(tab) => {
        setCurrentTab(tab);
        setSearchQuery('');
      }}
      activeAlertsCount={activeAlertsCount}
      networkDiagram={features.network_diagram_beta}
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
          onNavigateToDevices={admin ? () => setCurrentTab('devices') : undefined}
          onNavigateToAlerts={() => setCurrentTab('alerts')}
        />
      )}
      {tab === 'overview' && features.network_diagram_beta && <Suspense><NetworkDiagramWidget /></Suspense>}
      {tab === 'network-diagram' && admin && features.network_diagram_beta && <Suspense><NetworkDiagram /></Suspense>}
      {tab === 'devices' && <Devices searchQuery={searchQuery} />}
      {tab === 'checks' && <Checks />}
      {tab === 'alerts' && (features.incident_workspace ? <IncidentWorkspace /> : <Alerts />)}
      {tab === 'logs' && <Logs />}
      {tab === 'settings' && <Settings />}
    </AppLayout>
  );
};

