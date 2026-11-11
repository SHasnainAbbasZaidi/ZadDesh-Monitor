import React, { useEffect, useState } from 'react';
import { Card } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Modal } from '../components/ui/Modal';
import { EntryDetails, incidentTitle } from '../components/ui/EntryDetails';
import { api } from '../services/api';
import { useLiveUpdates } from '../services/live';
import { Alert } from '../types';
import { useAuth } from '../context/AuthContext';
import { ShieldAlert, CheckCircle2, Clock, MessageSquare, Check, RefreshCw } from 'lucide-react';

export const Alerts: React.FC = () => {
  const { user } = useAuth();
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'acknowledged' | 'resolved'>('active');

  // Ack Modal
  const [selectedAlert, setSelectedAlert] = useState<Alert | null>(null);
  const [ackComment, setAckComment] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const loadAlerts = async () => {
    try {
      setLoading(true);
      const res = await api.getAlerts(statusFilter === 'all' ? undefined : statusFilter);
      setAlerts(res);
      setError('');
    } catch (err) {
      setError('Could not load alerts. Retry using Refresh alerts.');
    } finally {
      setLoading(false);
    }
  };

  useLiveUpdates(() => { void loadAlerts(); });

  useEffect(() => {
    loadAlerts();
    const timer = window.setInterval(loadAlerts, 10_000);
    return () => window.clearInterval(timer);
  }, [statusFilter]);

  const handleAcknowledge = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedAlert) return;
    setIsSubmitting(true);
    try {
      await api.acknowledgeAlert(selectedAlert.id, ackComment || 'Acknowledged by operator');
      setSelectedAlert(null);
      setAckComment('');
      loadAlerts();
    } catch (err) {
      setError('Could not acknowledge this incident.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleResolve = async (id: number) => {
    try {
      await api.resolveAlert(id);
      loadAlerts();
    } catch (err) {
      setError('Could not resolve this incident.');
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">
            Alerts & Incidents
          </h1>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
            Active and historical threshold breaches, failures, and notifications
          </p>
        </div>
        <button
          aria-label="Refresh alerts"
          onClick={loadAlerts}
          className="p-2 rounded-xl border border-slate-200 dark:border-slate-800 text-slate-500 hover:text-slate-900 dark:hover:text-white bg-white dark:bg-slate-900 shadow-sm self-start sm:self-auto"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Filter Tabs */}
      <div className="flex items-center space-x-2">
        {(['active', 'acknowledged', 'resolved', 'all'] as const).map((st) => (
          <button
            key={st}
            onClick={() => setStatusFilter(st)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium capitalize transition-colors ${
              statusFilter === st
                ? 'bg-sky-700 text-white shadow-sm'
                : 'bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800'
            }`}
          >
            {st}
          </button>
        ))}
      </div>

      {error && <p role="alert" className="ops-notice">{error}</p>}
      {alerts.length === 0 ? (
        <Card>
          <div className="py-16 text-center text-slate-400">
            <CheckCircle2 className="w-12 h-12 mx-auto text-emerald-500/60 mb-3" />
            <p className="text-base font-semibold text-slate-900 dark:text-slate-100">
              {loading ? 'Loading alerts…' : 'No matching alerts'}
            </p>
            <p className="text-xs text-slate-500">Only incidents recorded by the core appear here.</p>
          </div>
        </Card>
      ) : (
        <div className="space-y-3">
          {alerts.map((al) => (
            <div
              key={al.id}
              className={`p-5 rounded-2xl bg-white dark:bg-slate-900 border transition-all ${
                al.status === 'active'
                  ? 'border-rose-300 dark:border-rose-900/60 shadow-sm shadow-rose-500/5'
                  : 'border-slate-200 dark:border-slate-800'
              }`}
            >
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="min-w-0 space-y-1">
                  <div className="flex items-center space-x-3">
                    <Badge status={al.severity} />
                    <span className="text-xs font-mono uppercase px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-500">
                      {al.status}
                    </span>
                  </div>
                  <EntryDetails title={incidentTitle(al.title)}>
                    <div>{al.title}</div>{al.message && <div>{al.message}</div>}
                  </EntryDetails>
                  <div className="text-[11px] font-mono text-slate-400 flex items-center space-x-2 pt-1">
                    <Clock className="w-3 h-3" />
                    <span>Triggered: {new Date(al.triggered_at).toLocaleString()}</span>
                    {al.resolved_at && (
                      <span>• Resolved: {new Date(al.resolved_at).toLocaleString()}</span>
                    )}
                  </div>
                </div>

                {user?.role === 'admin' && al.status !== 'resolved' && (
                  <div className="flex items-center space-x-2 shrink-0">
                    {al.status === 'active' && (
                      <button
                        onClick={() => setSelectedAlert(al)}
                        className="px-3 py-1.5 rounded-lg border border-slate-200 dark:border-slate-700 text-xs font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
                      >
                        Acknowledge
                      </button>
                    )}
                    <button
                      onClick={() => handleResolve(al.id)}
                      className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-medium shadow-sm transition-colors"
                    >
                      Resolve
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Acknowledge Modal */}
      <Modal
        isOpen={!!selectedAlert}
        onClose={() => setSelectedAlert(null)}
        title="Acknowledge Alert"
      >
        <form onSubmit={handleAcknowledge} className="space-y-4">
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Acknowledging halts notification escalations for: <br />
            <strong className="text-slate-900 dark:text-slate-100">{selectedAlert && incidentTitle(selectedAlert.title)}</strong>
          </p>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-1">
              Operator Comment
            </label>
            <textarea aria-label="Operator comment"
              required
              rows={3}
              value={ackComment}
              onChange={(e) => setAckComment(e.target.value)}
              placeholder="e.g. Investigating interface CRC errors on uplink port 2..."
              className="w-full p-3 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-sm outline-none focus:border-sky-500"
            />
          </div>

          <div className="flex justify-end space-x-3 pt-2">
            <button
              type="button"
              onClick={() => setSelectedAlert(null)}
              className="px-4 py-2 rounded-xl text-sm text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="px-5 py-2 bg-sky-600 hover:bg-sky-700 text-white rounded-xl text-sm font-medium shadow-md transition-all disabled:opacity-50"
            >
              {isSubmitting ? 'Submitting...' : 'Confirm Acknowledgement'}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
};


