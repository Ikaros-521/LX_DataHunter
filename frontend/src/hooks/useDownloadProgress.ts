import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { DownloadTask } from '../api/queries';

/**
 * 订阅下载进度 WebSocket，实时更新 React Query 缓存中的任务状态。
 */
export default function useDownloadProgress() {
  const queryClient = useQueryClient();
  const wsRef = useRef<WebSocket | null>(null);
  const heartbeatRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/downloads`;
    let reconnectTimeout: ReturnType<typeof setTimeout>;

    function connect() {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        // 心跳保持连接
        heartbeatRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send('ping');
          }
        }, 30000);
      };

      ws.onmessage = (event) => {
        if (event.data === 'pong') return;
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'heartbeat') return;

          // 更新 React Query 缓存
          updateTaskInCache(data);
        } catch {
          // ignore
        }
      };

      ws.onclose = () => {
        clearInterval(heartbeatRef.current);
        // 3秒后重连
        reconnectTimeout = setTimeout(connect, 3000);
      };

      ws.onerror = () => {
        ws.close();
      };
    }

    function updateTaskInCache(data: DownloadTask) {
      queryClient.setQueriesData<{ items: DownloadTask[] }>(
        { queryKey: ['downloads'] },
        (old) => {
          if (!old) return old;
          return {
            ...old,
            items: old.items.map((t) =>
              t.id === data.id ? { ...t, ...data } : t
            ),
          };
        }
      );
    }

    connect();

    return () => {
      clearTimeout(reconnectTimeout);
      clearInterval(heartbeatRef.current);
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [queryClient]);
}
