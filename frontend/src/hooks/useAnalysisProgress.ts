import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { VideoAnalysis } from '../api/queries';

/**
 * 订阅 /ws/analysis，实时更新分析任务缓存。
 */
export default function useAnalysisProgress() {
  const queryClient = useQueryClient();
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/analysis`;
    let reconnect: ReturnType<typeof setTimeout>;

    function connect() {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;
      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          update(data);
        } catch {
          /* ignore */
        }
      };
      ws.onclose = () => { reconnect = setTimeout(connect, 3000); };
      ws.onerror = () => ws.close();
    }

    function update(patch: Partial<VideoAnalysis> & { id: number }) {
      queryClient.setQueriesData<{ items: VideoAnalysis[] }>(
        { queryKey: ['analysis'] },
        (old) => old ? { ...old, items: old.items.map(t => t.id === patch.id ? { ...t, ...patch } : t) } : old,
      );
      queryClient.setQueriesData<VideoAnalysis>(
        { queryKey: ['analysis', 'detail'] },
        (old) => old && old.id === patch.id ? { ...old, ...patch } : old,
      );
    }

    connect();
    return () => { clearTimeout(reconnect); wsRef.current?.close(); };
  }, [queryClient]);
}
