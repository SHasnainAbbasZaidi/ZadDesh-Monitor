import { ChevronRight } from 'lucide-react';
import type { ReactNode } from 'react';

export function shortEntryTitle(text: string, fallback: string) {
  const firstLine = text.trim().split(/\r?\n/, 1)[0]?.replace(/\s+/g, ' ') || fallback;
  if (firstLine.length <= 64) return firstLine;
  const prefix = firstLine.slice(0, 64);
  const boundary = prefix.lastIndexOf(' ');
  return (boundary > 32 ? prefix.slice(0, boundary) : prefix) + '…';
}

export function logTitle(message: string) {
  if (/\b(?:link|line protocol)\s+(?:(?:is|went|now)\s+)?down\b|\binterface\b.*\bchanged (?:state )?to down\b/i.test(message)) return 'Link down';
  if (/\bspeed\s+(?:has\s+)?changed\b|\bchanged\s+(?:the\s+)?(?:link\s+)?speed\b/i.test(message)) return 'Speed changed';
  if (/\bhigh memory usage\b|\bmemory usage\s+(?:is high|exceeded|above)\b/i.test(message)) return 'High memory usage';
  return shortEntryTitle(message, 'Log entry');
}

export function incidentTitle(title: string, message?: string, event = false) {
  if (/^Availability:\s/i.test(title)) return event && message === 'Probe passed' ? 'Probe recovered' : 'Probe down';
  return shortEntryTitle(title, event ? 'Event' : 'Alert');
}

export function EntryDetails({ title, children }: { title: string; children: ReactNode }) {
  return <details className="entry-details min-w-0">
    <summary><ChevronRight size={14} aria-hidden="true" /><strong>{title}</strong></summary>
    <div className="entry-detail-body">{children}</div>
  </details>;
}
