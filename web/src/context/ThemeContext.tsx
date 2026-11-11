import React, { createContext, useContext, useEffect, useLayoutEffect, useState } from 'react';

type Theme = 'dark' | 'light';
type Preference = Theme | 'system';
const ThemeContext = createContext<{
  theme: Theme;
  preference: Preference;
  setPreference: (value: Preference) => void;
} | undefined>(undefined);

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [preference, setPreference] = useState<Preference>(() => {
    const saved = localStorage.getItem('zaddesh_theme');
    return saved === 'light' || saved === 'dark' ? saved : 'system';
  });
  const [systemDark, setSystemDark] = useState(() => window.matchMedia('(prefers-color-scheme: dark)').matches);
  useEffect(() => {
    const query = window.matchMedia('(prefers-color-scheme: dark)');
    const changed = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    query.addEventListener('change', changed);
    return () => query.removeEventListener('change', changed);
  }, []);
  const theme = preference === 'system' ? (systemDark ? 'dark' : 'light') : preference;
  useLayoutEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
    document.documentElement.style.colorScheme = theme;
  }, [theme]);
  useEffect(() => {
    localStorage.setItem('zaddesh_theme', preference);
  }, [preference]);
  return <ThemeContext.Provider value={{ theme, preference, setPreference }}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) throw new Error('useTheme must be used within ThemeProvider');
  return context;
}
