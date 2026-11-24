import React from 'react';
import {
  LayoutDashboard,
  Server,
  Activity,
  AlertTriangle,
  ScrollText,
  Network,
  Settings as SettingsIcon,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';

interface SidebarProps {
  open: boolean;
  currentTab: string;
  onSelectTab: (tab: string) => void;
  activeAlertsCount: number;
  networkDiagram?: boolean;
}

export const Sidebar: React.FC<SidebarProps> = ({
  open,
  currentTab,
  onSelectTab,
  activeAlertsCount,
  networkDiagram,
}) => {
  const { user } = useAuth();
  const navItems = [
    { id: 'overview', label: 'Overview', icon: LayoutDashboard },
    { id: 'devices', label: 'Devices', icon: Server },
    { id: 'discovered', label: 'Discovered Devices', icon: Network },
    { id: 'checks', label: 'Checks', icon: Activity },
    { id: 'endpoint-trace', label: 'Live Endpoint Trace', icon: Network },
    { id: 'custom-dashboards', label: 'Custom Dashboards', icon: LayoutDashboard },
    { id: 'logs', label: 'Infrastructure Logs', icon: ScrollText },
    {
      id: 'alerts',
      label: 'Alerts & Events',
      icon: AlertTriangle,
      badge: activeAlertsCount > 0 ? activeAlertsCount : null,
      badgeColor: 'bg-rose-700 text-white',
    },
    ...(networkDiagram && user?.role === 'admin' ? [{ id: 'network-diagram', label: 'Network Diagram', icon: Network }] : []),
    { id: 'settings', label: 'Settings', icon: SettingsIcon },
  ].filter(item => user?.role === 'admin' || ['overview', 'logs', 'alerts'].includes(item.id));

  return (
    <aside id="app-sidebar" className={open ? 'ops-chrome w-full md:w-56 border-b md:border-b-0 md:border-r flex flex-col shrink-0 select-none' : 'hidden'}>
      {/* Navigation Links */}
      <nav aria-label="Main navigation" className="flex md:block md:flex-1 gap-1 px-2 py-2 md:px-3 md:py-4 md:space-y-1 overflow-auto">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = currentTab === item.id;

          return (
            <button
              key={item.id}
              aria-current={isActive ? "page" : undefined}
              onClick={() => onSelectTab(item.id)}
              className="ops-nav-link shrink-0 md:w-full flex items-center justify-between px-2 md:px-3 py-2.5 rounded-lg text-sm font-medium"
            >
              <div className="flex items-center gap-1.5 md:gap-3">
                <Icon className="w-4 h-4" />
                <span className="text-xs md:text-sm">{item.label}</span>
              </div>
              {item.badge && (
                <span
                  className={`px-2 py-0.5 rounded-full text-xs font-semibold ${item.badgeColor}`}
                >
                  {item.badge}
                </span>
              )}

            </button>
          );
        })}
      </nav>

    </aside>
  );
};

