/**
 * SSE 流式对话消费器。
 *
 * 用原生 fetch + ReadableStream 消费后端 /analysis/chat/stream 的
 * text/event-stream，绕过 axios（XHR 不支持流式响应体 + 30s timeout）。
 */
import type { AnalysisChatParams } from './queries';

export interface StreamCallbacks {
  /** 收到一个 token 片段 */
  onToken: (token: string) => void;
  /** 流正常结束，model 为使用的 LLM 模型名 */
  onDone?: (model?: string) => void;
  /** 出错（网络/服务端 error 事件/非 200） */
  onError?: (e: Error) => void;
}

/**
 * 发起流式对话。
 * @param params 请求参数（与 useAnalysisChat 一致）
 * @param cb     流式回调
 * @param signal 可选 AbortSignal，用于中断
 */
export async function streamChat(
  params: AnalysisChatParams,
  cb: StreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch('/api/v1/analysis/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
      signal,
    });
  } catch (e) {
    // 用户主动中断时 fetch 抛 AbortError，不当作错误
    if ((e as Error).name === 'AbortError') return;
    cb.onError?.(e as Error);
    return;
  }

  if (!resp.ok || !resp.body) {
    cb.onError?.(new Error(`流式请求失败: HTTP ${resp.status}`));
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE 事件以 \n\n 分隔，逐事件处理
      let sep: number;
      while ((sep = buffer.indexOf('\n\n')) >= 0) {
        const rawEvent = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        // 一个事件内可能多行，取 data: 开头的行
        const dataLines = rawEvent
          .split('\n')
          .filter((l) => l.startsWith('data:'))
          .map((l) => l.slice(5).trim());
        if (!dataLines.length) continue;
        const payload = dataLines.join('\n');
        if (payload === '[DONE]') {
          cb.onDone?.();
          return;
        }
        let obj: { token?: string; done?: boolean; llm_model?: string; error?: string };
        try {
          obj = JSON.parse(payload);
        } catch {
          continue;
        }
        if (obj.error) {
          cb.onError?.(new Error(obj.error));
          return;
        }
        if (obj.token) {
          cb.onToken(obj.token);
        }
        if (obj.done) {
          cb.onDone?.(obj.llm_model);
          return;
        }
      }
    }
    // 流自然结束但没收到 done 事件
    cb.onDone?.();
  } catch (e) {
    if ((e as Error).name === 'AbortError') return;
    cb.onError?.(e as Error);
  }
}
