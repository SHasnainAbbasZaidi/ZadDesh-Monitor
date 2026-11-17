import React from 'react';
import {
  LayoutDashboard,
  Server,
  Activity,
  AlertTriangle,
  ScrollText,
  Network,
  Mail,
  Settings as SettingsIcon,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';

interface SidebarProps {
  currentTab: string;
  onSelectTab: (tab: string) => void;
  activeAlertsCount: number;
  networkDiagram?: boolean;
}

export const Sidebar: React.FC<SidebarProps> = ({
  currentTab,
  onSelectTab,
  activeAlertsCount,
  networkDiagram,
}) => {
  const { user } = useAuth();
  const navItems = [
    { id: 'overview', label: 'Overview', icon: LayoutDashboard },
    { id: 'devices', label: 'Devices', icon: Server },
    { id: 'checks', label: 'Checks', icon: Activity },
    { id: 'logs', label: 'Infrastructure Logs', icon: ScrollText },
    {
      id: 'alerts',
      label: 'Alerts & Events',
      icon: AlertTriangle,
      badge: activeAlertsCount > 0 ? activeAlertsCount : null,
      badgeColor: 'bg-rose-700 text-white',
    },
    { id: 'email-alerts', label: 'Email Alerts & Notifications', icon: Mail },
    ...(networkDiagram && user?.role === 'admin' ? [{ id: 'network-diagram', label: 'Network Diagram', icon: Network }] : []),
    { id: 'settings', label: 'Settings', icon: SettingsIcon },
  ].filter(item => user?.role === 'admin' || ['overview', 'logs', 'alerts'].includes(item.id));

  return (
    <aside className="ops-chrome w-full md:w-56 border-b md:border-b-0 md:border-r flex flex-col md:h-dvh shrink-0 select-none">
      {/* Brand Header */}
      <div className="ops-chrome h-12 md:h-16 px-4 md:px-5 border-b flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-sky-600 to-cyan-400 flex items-center justify-center text-white font-black text-lg shadow-md shadow-sky-500/20">
            Z
          </div>
          <div>
            <span className="font-bold text-base tracking-tight text-slate-900 dark:text-white">
              Zad<span className="text-sky-500">Desh</span>
            </span>
            <span className="block text-[10px] uppercase font-mono tracking-wider text-slate-400">
              Infrastructure
            </span>
          </div>
        </div>
        <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-sky-100 dark:bg-sky-950 text-sky-700 dark:text-sky-400 font-semibold border border-sky-300 dark:border-sky-800">
          v0.1
        </span>
      </div>

      {/* Navigation Links */}
      <nav aria-label="Main navigation" className="flex md:block md:flex-1 gap-1 px-2 py-2 md:px-3 md:py-4 md:space-y-1 overflow-auto">
        <div className="hidden md:block px-3 pb-2 text-[11px] font-semibold uppercase tracking-wider ops-muted font-mono">
          Monitoring
        </div>
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

