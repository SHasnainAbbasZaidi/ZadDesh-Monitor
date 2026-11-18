import { useEffect, useRef } from 'react';

export function useAddonUpdates(refresh: () => void, paused = false) {
  const callback = useRef(refresh);
  useEffect(() => { callback.current = refresh; }, [refresh]);
  useEffect(() => {
    const token = localStorage.getItem('zaddesh_token');
    if (!token || paused) return;
    const url = new URL('/api/v1/addon-updates', window.location.href);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    let socket: WebSocket;
    let stopped = false;
    let retry = 1000;
    let timer: number;
    let refreshTimer: number | undefined;
    let lastRefresh = Date.now();
    function scheduleRefresh() {
      if (refreshTimer !== undefined || stopped) return;
      refreshTimer = window.setTimeout(() => {
        refreshTimer = undefined;
        lastRefresh = Date.now();
        callback.current();
      }, Math.max(0, 10000 - (Date.now() - lastRefresh)));
    }
    function connect() {
      socket = new WebSocket(url, ['zaddesh-v1', token!]);
      socket.onmessage = () => { retry = 1000; scheduleRefresh(); };
      socket.onclose = () => {
        if (!stopped) {
          timer = window.setTimeout(connect, retry);
          retry = Math.min(retry * 2, 15000);
        }
      };
    }
    connect();
    const poll = window.setInterval(scheduleRefresh, 15000);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      window.clearTimeout(refreshTimer);
      window.clearInterval(poll);
      socket.close();
    };
  }, [paused]);
}
