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
        <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <input
          ref={search}
          type="search"
          aria-label="Search devices"
          aria-keyshortcuts="Control+K Meta+K"
          placeholder="Search devices…"
          onChange={(e) => onSearchChange(e.target.value)}
          className="w-full pl-9 pr-4 py-1.5 text-sm bg-slate-100 dark:bg-slate-800/80 border border-transparent focus:border-sky-500 rounded-lg text-slate-900 dark:text-slate-100 placeholder-slate-400 outline-none transition-all"
        />
      </div> : <div />}

      {/* Right Controls */}
      <div className="flex items-center space-x-3">
        <select className="ops-button" value={preference} aria-label="Color theme"
          onChange={event => setPreference(event.target.value as typeof preference)}>
          <option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option>
        </select>

        <div className="h-4 w-px bg-slate-200 dark:bg-slate-800 mx-1" />

        {/* User Info */}
        <div className="flex items-center space-x-3">
          <div className="flex items-center space-x-2">
            <div className="w-7 h-7 rounded-full bg-sky-500/10 border border-sky-500/20 text-sky-600 dark:text-sky-400 font-bold text-xs flex items-center justify-center">
              {user?.username?.substring(0, 2).toUpperCase() || 'U'}
            </div>
            <div className="text-left hidden sm:block">
              <div className="text-xs font-semibold text-slate-900 dark:text-slate-100 leading-tight">
                {user?.username}
              </div>
              <div className="text-[10px] font-mono text-slate-400 flex items-center space-x-1">
                <Shield className="w-2.5 h-2.5 text-sky-500" />
                <span>{user?.role === 'admin' ? 'Admin' : 'User'}</span>
              </div>
            </div>
          </div>

          {/* Logout Button */}
          <button
            onClick={logout}
            title="Sign out"
            aria-label="Sign out"
            className="p-2 rounded-lg text-slate-400 hover:text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950/30 transition-colors"
          >
            <LogOut className="w-4 h-4" />
          </button>
        </div>
      </div>
    </header>
  );
};

