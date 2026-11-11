import { useEffect, useRef, useState } from 'react';

type Connection = 'connecting' | 'connected' | 'retrying' | 'paused';

export function useLiveUpdates(onChange: () => void, paused = false) {
  const listener = useRef(onChange);
  useEffect(() => { listener.current = onChange; }, [onChange]);
  const [state, setState] = useState<Connection>('connecting');

  useEffect(() => {
    if (paused) return;
    const abort = new AbortController();
    let timer: number | undefined;
    let attempts = 0;
    let lastChange = 0;
    async function connect() {
      const token = localStorage.getItem('zaddesh_token');
      if (!token) return;
      try {
        const response = await fetch('/api/v1/stream', {
          headers: { Authorization: `Bearer ${token}` },
          signal: abort.signal,
        });
        if (!response.ok || !response.body) throw new Error('Stream unavailable');
        const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
        let buffer = '';
        for (;;) {
          const { done, value } = await reader.read();
          if (done || abort.signal.aborted) break;
          buffer += value;
          let delimiter: number;
          while ((delimiter = buffer.indexOf('\n\n')) >= 0) {
            const event = buffer.slice(0, delimiter);
            buffer = buffer.slice(delimiter + 2);
            if (event.startsWith('event: ready')) {
              attempts = 0;
              setState('connected');
            } else if (event.startsWith('event: change') && Date.now() - lastChange >= 2000) {
              lastChange = Date.now();
              listener.current();
            }
          }
        }
      } catch {
        // Snapshot polling remains available when the streaming connection drops.
      }
      if (!abort.signal.aborted) {
        setState('retrying');
        const delay = Math.min(30_000, 1000 * 2 ** Math.min(attempts++, 5));
        timer = window.setTimeout(connect, delay + Math.random() * 500);
      }
    }
    void connect();
    return () => { abort.abort(); window.clearTimeout(timer); };
  }, [paused]);

  return paused ? 'paused' : state;
}
