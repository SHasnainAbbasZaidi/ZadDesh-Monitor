import React from 'react';

interface BadgeProps {
  status?: string;
  children?: React.ReactNode;
  className?: string;
}

export const Badge: React.FC<BadgeProps> = ({ status, children, className = '' }) => {
  const s = (status || children || '').toString().toLowerCase();

  let colorClasses = 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300 border-slate-200 dark:border-slate-700';

  if (['up', 'ok', 'online', 'resolved', 'healthy'].includes(s)) {
    colorClasses = 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-400 dark:border-emerald-800/60';
  } else if (['down', 'critical', 'failed'].includes(s)) {
    colorClasses = 'bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-950/60 dark:text-rose-400 dark:border-rose-800/60';
  } else if (['warning', 'degraded', 'average', 'high'].includes(s)) {
    colorClasses = 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/60 dark:text-amber-400 dark:border-amber-800/60';
  } else if (['info', 'acknowledged'].includes(s)) {
    colorClasses = 'bg-sky-50 text-sky-700 border-sky-200 dark:bg-sky-950/60 dark:text-sky-400 dark:border-sky-800/60';
  }

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border uppercase tracking-wider ${colorClasses} ${className}`}
    >
      <span
        className={`w-1.5 h-1.5 mr-1.5 rounded-full ${
          ['up', 'ok', 'online', 'healthy'].includes(s)
            ? 'bg-emerald-500 animate-pulse'
            : ['down', 'critical'].includes(s)
            ? 'bg-rose-500 animate-ping'
            : ['warning', 'degraded'].includes(s)
            ? 'bg-amber-500'
            : 'bg-slate-400'
        }`}
      />
      {children || status}
    </span>
  );
};

