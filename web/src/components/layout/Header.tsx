import React, { useEffect, useRef } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useTheme } from '../../context/ThemeContext';
import { LogOut, Menu, Shield, Search } from 'lucide-react';

interface HeaderProps {
  onSearchChange?: (term: string) => void;
  sidebarOpen: boolean;
  onToggleSidebar: () => void;
}

export const Header: React.FC<HeaderProps> = ({ onSearchChange, sidebarOpen, onToggleSidebar }) => {
  const { user, logout } = useAuth();
  const { preference, setPreference } = useTheme();
  const search = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!onSearchChange) return;
    const handle = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        search.current?.focus();
      }
    };
    window.addEventListener('keydown', handle);
    return () => window.removeEventListener('keydown', handle);
  }, [onSearchChange]);

  return (
    <header className="ops-chrome grid min-h-16 shrink-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-2 border-b px-3 py-2 sm:grid-cols-[minmax(0,1fr)_8rem_minmax(0,1fr)] sm:px-5 md:grid-cols-[minmax(0,1fr)_14rem_minmax(0,1fr)] lg:grid-cols-[minmax(0,1fr)_20rem_minmax(0,1fr)]">
        <div className="col-start-1 row-start-1 flex items-center gap-2 sm:gap-3">
          <button type="button" className="ops-button dash-icon-button shrink-0" aria-controls="app-sidebar" aria-expanded={sidebarOpen}
            aria-label={sidebarOpen ? 'Hide sidebar' : 'Show sidebar'} title={sidebarOpen ? 'Hide sidebar' : 'Show sidebar'} onClick={onToggleSidebar}>
            <Menu size={18} aria-hidden="true" />
          </button>
          <div className="flex items-center gap-2 whitespace-nowrap">
            <div aria-hidden="true" className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-tr from-sky-600 to-cyan-400 text-lg font-black text-white shadow-md shadow-sky-500/20">Z</div>
            <span className="sr-only sm:not-sr-only text-base font-bold tracking-tight text-slate-900 dark:text-white">
              Zad<span className="text-sky-500">Desh</span>
            </span>
          </div>
        </div>
        {onSearchChange && <div className="relative col-span-2 col-start-1 row-start-2 w-full max-w-xs justify-self-center sm:col-span-1 sm:col-start-2 sm:row-start-1">
          <Search aria-hidden="true" className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            ref={search}
            type="search"
            aria-label="Search devices"
            aria-keyshortcuts="Control+K Meta+K"
            placeholder="Search devices…"
            onChange={(e) => onSearchChange(e.target.value)}
            className="ops-header-search w-full pl-9 pr-4 py-1.5 text-sm rounded-lg"
          />
        </div>}

      {/* Right Controls */}
      <div className="col-start-2 row-start-1 flex max-w-full items-center justify-self-end gap-2 sm:col-start-3 sm:gap-3">
        <select className="ops-button" value={preference} aria-label="Color theme"
          onChange={event => setPreference(event.target.value as typeof preference)}>
          <option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option>
        </select>

        <div aria-hidden="true" className="ops-account-divider h-4 w-px" />

        {/* User Info */}
        <div className="flex min-w-0 items-center gap-2 sm:gap-3">
          {user && <div className="flex min-w-0 items-center gap-2" title={`${user.username} · ${user.role === 'admin' ? 'Admin' : 'User'}`}>
            <span aria-hidden="true" className="ops-account-initials w-7 h-7 shrink-0 rounded-md font-semibold text-xs grid place-items-center">
              {user.username.slice(0, 2).toUpperCase()}
            </span>
            <div className="sr-only min-w-0 sm:not-sr-only text-left">
              <div className="max-w-28 truncate text-xs font-semibold leading-tight">
                {user.username}
              </div>
              <div className="text-[11px] ops-muted flex items-center gap-1">
                <Shield aria-hidden="true" className="w-3 h-3" />
                <span>{user.role === 'admin' ? 'Admin' : 'User'}</span>
              </div>
            </div>
          </div>}

          {/* Logout Button */}
          <button
            onClick={logout}
            title="Sign out"
            aria-label="Sign out"
            className="ops-sign-out shrink-0 p-2 rounded-lg"
          >
            <LogOut aria-hidden="true" className="w-4 h-4" />
          </button>
        </div>
      </div>
    </header>
  );
};

