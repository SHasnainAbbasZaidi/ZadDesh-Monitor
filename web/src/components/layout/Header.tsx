import React, { useEffect, useRef } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useTheme } from '../../context/ThemeContext';
import { LogOut, Shield, Search } from 'lucide-react';

interface HeaderProps {
  onSearchChange?: (term: string) => void;
}

export const Header: React.FC<HeaderProps> = ({ onSearchChange }) => {
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
    <header className="ops-chrome h-16 border-b px-3 sm:px-6 flex items-center justify-between gap-2 shrink-0 sticky top-0 z-30">
      {/* Search Input */}
      {onSearchChange ? <div className="relative flex-1 max-w-xs">
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
      </div> : <div />}

      {/* Right Controls */}
      <div className="flex items-center gap-2 sm:gap-3 shrink-0">
        <select className="ops-button" value={preference} aria-label="Color theme"
          onChange={event => setPreference(event.target.value as typeof preference)}>
          <option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option>
        </select>

        <div aria-hidden="true" className="ops-account-divider h-4 w-px" />

        {/* User Info */}
        <div className="flex items-center gap-2 sm:gap-3">
          {user && <div className="flex items-center gap-2" title={`${user.username} · ${user.role === 'admin' ? 'Admin' : 'User'}`}>
            <span aria-hidden="true" className="ops-account-initials w-7 h-7 rounded-md font-semibold text-xs grid place-items-center">
              {user.username.slice(0, 2).toUpperCase()}
            </span>
            <div className="sr-only sm:not-sr-only text-left">
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
            className="ops-sign-out p-2 rounded-lg"
          >
            <LogOut aria-hidden="true" className="w-4 h-4" />
          </button>
        </div>
      </div>
    </header>
  );
};

