import React, { useEffect, useState } from 'react';
import { Sidebar } from './Sidebar';
import { Header } from './Header';

interface AppLayoutProps {
  currentTab: string;
  onSelectTab: (tab: string) => void;
  activeAlertsCount: number;
  networkDiagram?: boolean;
  onSearchChange?: (term: string) => void;
  children: React.ReactNode;
}

export const AppLayout: React.FC<AppLayoutProps> = ({
  currentTab,
  onSelectTab,
  activeAlertsCount,
  networkDiagram,
  onSearchChange,
  children,
}) => {
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    try { return localStorage.getItem('zaddesh-sidebar') !== 'hidden'; }
    catch { return true; }
  });
  useEffect(() => {
    try { localStorage.setItem('zaddesh-sidebar', sidebarOpen ? 'open' : 'hidden'); }
    catch { /* The sidebar can still be toggled in this session. */ }
  }, [sidebarOpen]);

  return (
    <div className="ops-shell flex h-dvh w-full flex-col overflow-hidden">
      <Header onSearchChange={onSearchChange} sidebarOpen={sidebarOpen} onToggleSidebar={() => setSidebarOpen(open => !open)} />
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden md:flex-row">
        <Sidebar
          open={sidebarOpen}
          currentTab={currentTab}
          onSelectTab={onSelectTab}
          activeAlertsCount={activeAlertsCount}
          networkDiagram={networkDiagram}
        />
        <main id="main-content" className="min-w-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6 lg:px-8">
          <div className={currentTab === 'network-diagram' || !sidebarOpen ? 'w-full space-y-6' : 'max-w-screen-2xl mx-auto space-y-6'}>{children}</div>
        </main>
      </div>
    </div>
  );
};

