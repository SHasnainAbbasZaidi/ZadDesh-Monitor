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
    function connect() {
      socket = new WebSocket(url, ['zaddesh-v1', token!]);
      socket.onmessage = () => { retry = 1000; callback.current(); };
      socket.onclose = () => {
        if (!stopped) {
          timer = window.setTimeout(connect, retry);
          retry = Math.min(retry * 2, 15000);
        }
      };
    }
    connect();
    const poll = window.setInterval(() => callback.current(), 15000);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      window.clearInterval(poll);
      socket.close();
    };
  }, [paused]);
}
