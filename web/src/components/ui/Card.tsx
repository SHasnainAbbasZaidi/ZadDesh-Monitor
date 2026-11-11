import React from 'react';

interface CardProps {
  title?: string;
  subtitle?: string;
  headerAction?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

export const Card: React.FC<CardProps> = ({
  title,
  subtitle,
  headerAction,
  children,
  className = '',
}) => {
  return (
    <div
      className={`bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl shadow-sm overflow-hidden ${className}`}
    >
      {(title || headerAction) && (
        <div className="px-5 py-4 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between">
          <div>
            {title && (
              <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">
                {title}
              </h3>
            )}
            {subtitle && (
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                {subtitle}
              </p>
            )}
          </div>
          {headerAction && <div>{headerAction}</div>}
        </div>
      )}
      <div className="p-5">{children}</div>
    </div>
  );
};

