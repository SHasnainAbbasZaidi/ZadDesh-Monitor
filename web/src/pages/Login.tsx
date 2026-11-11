import React, { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { useTheme } from '../context/ThemeContext';
import { api } from '../services/api';
import { ShieldCheck, Server, Lock, User, Mail, Sun, Moon } from 'lucide-react';

export const Login: React.FC = () => {
  const { login, isSetupRequired } = useAuth();
  const { theme, setPreference } = useTheme();

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [email, setEmail] = useState('');
  const [fullName, setFullName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsSubmitting(true);

    try {
      if (isSetupRequired) {
        const res = await api.setupAdmin({
          username,
          email,
          password,
          full_name: fullName || undefined,
        });
        login(res.access_token, res.user);
      } else {
        const res = await api.login({ username, password });
        login(res.access_token, res.user);
      }
    } catch (err: any) {
      setError(err.message || 'Authentication failed');
    } finally {
      setIsSubmitting(false);
    }
  };


  return (
    <div className="min-h-screen w-screen flex items-center justify-center p-4 bg-slate-100 dark:bg-slate-950 text-slate-900 dark:text-slate-100 relative">
      {/* Theme Switcher in top corner */}
      <button
        onClick={() => setPreference(theme === 'dark' ? 'light' : 'dark')}
        className="absolute top-6 right-6 p-2 rounded-xl bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white shadow-sm"
      >
        {theme === 'dark' ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
      </button>

      <div className="w-full max-w-md bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl shadow-xl p-8 space-y-6">
        {/* Header */}
        <div className="text-center space-y-2">
          <div className="inline-flex w-14 h-14 rounded-2xl bg-gradient-to-tr from-sky-600 to-cyan-400 items-center justify-center text-white text-2xl font-black shadow-lg shadow-sky-500/25 mb-1">
            Z
          </div>
          <h1 className="text-2xl font-bold tracking-tight">
            {isSetupRequired ? 'Initialize ZadDesh' : 'Sign in to ZadDesh'}
          </h1>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {isSetupRequired
              ? 'Create the initial Administrator account to begin monitoring.'
              : 'Enterprise self-hosted infrastructure observability'}
          </p>
        </div>

        {error && (
          <div className="p-3 text-xs bg-rose-500/10 border border-rose-500/30 text-rose-600 dark:text-rose-400 rounded-xl">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="login-username" className="block text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-1">
              Username
            </label>
            <div className="relative">
              <User className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input id="login-username"
                type="text"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="e.g. admin"
                className="w-full pl-9 pr-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm outline-none focus:border-sky-500 transition-colors"
              />
            </div>
          </div>

          {isSetupRequired && (
            <>
              <div>
                <label htmlFor="login-email" className="block text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-1">
                  Email Address
                </label>
                <div className="relative">
                  <Mail className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                  <input id="login-email"
                    type="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="admin@yourdomain.com"
                    className="w-full pl-9 pr-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm outline-none focus:border-sky-500 transition-colors"
                  />
                </div>
              </div>
              <div>
                <label htmlFor="login-full" className="block text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-1">
                  Full Name (Optional)
                </label>
                <input id="login-full"
                  type="text"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder="System Administrator"
                  className="w-full px-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm outline-none focus:border-sky-500 transition-colors"
                />
              </div>
            </>
          )}

          <div>
            <label htmlFor="login-password" className="block text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-1">
              Password
            </label>
            <div className="relative">
              <Lock className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input id="login-password"
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className="w-full pl-9 pr-4 py-2.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm outline-none focus:border-sky-500 transition-colors"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={isSubmitting}
            className="w-full py-3 px-4 bg-sky-600 hover:bg-sky-500 active:bg-sky-700 text-white font-medium text-sm rounded-xl shadow-lg shadow-sky-600/25 transition-all disabled:opacity-50"
          >
            {isSubmitting
              ? 'Authenticating...'
              : isSetupRequired
              ? 'Complete Setup & Launch'
              : 'Sign In'}
          </button>
        </form>

      </div>
    </div>
  );
};


