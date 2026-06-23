import { useEffect, useState, useRef } from 'react';

interface ProgressEvent {
  type: 'progress';
  current: number;
  total: number;
  status: string;
}

export function useCrawlProgress(sessionId: number | null) {
  const [progress, setProgress] = useState<ProgressEvent | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!sessionId) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/crawl/${sessionId}`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === 'progress') {
        setProgress(data);
      }
    };

    ws.onerror = () => console.warn('WebSocket error for session', sessionId);

    return () => {
      ws.close();
    };
  }, [sessionId]);

  return progress;
}
