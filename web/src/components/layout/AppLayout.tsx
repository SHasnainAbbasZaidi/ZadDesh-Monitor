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
    <div className="ops-shell flex h-dvh w-full flex-col md:flex-row overflow-hidden">
      <Sidebar
        open={sidebarOpen}
        currentTab={currentTab}
        onSelectTab={onSelectTab}
        activeAlertsCount={activeAlertsCount}
        networkDiagram={networkDiagram}
      />
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <Header onSearchChange={onSearchChange} sidebarOpen={sidebarOpen} onToggleSidebar={() => setSidebarOpen(open => !open)} />
        <main id="main-content" className="flex-1 overflow-y-auto px-4 py-5 sm:px-6 lg:px-8">
          <div className={currentTab === 'network-diagram' || !sidebarOpen ? 'w-full space-y-6' : 'max-w-screen-2xl mx-auto space-y-6'}>{children}</div>
        </main>
      </div>
    </div>
  );
};

