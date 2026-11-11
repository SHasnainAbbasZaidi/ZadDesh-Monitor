import React from 'react';
import { Sidebar } from './Sidebar';
import { Header } from './Header';

interface AppLayoutProps {
  currentTab: string;
  onSelectTab: (tab: string) => void;
  activeAlertsCount: number;
  onSearchChange?: (term: string) => void;
  children: React.ReactNode;
}

export const AppLayout: React.FC<AppLayoutProps> = ({
  currentTab,
  onSelectTab,
  activeAlertsCount,
  onSearchChange,
  children,
}) => {
  return (
    <div className="ops-shell flex h-dvh w-full flex-col md:flex-row overflow-hidden">
      <Sidebar
        currentTab={currentTab}
        onSelectTab={onSelectTab}
        activeAlertsCount={activeAlertsCount}
      />
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <Header onSearchChange={onSearchChange} />
        <main id="main-content" className="flex-1 overflow-y-auto px-4 py-5 sm:px-6 lg:px-8">
          <div className="max-w-screen-2xl mx-auto space-y-6">{children}</div>
        </main>
      </div>
    </div>
  );
};

