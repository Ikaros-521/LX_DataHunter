import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import client from './client';

// --- Types ---
export interface Platform {
  id: string; name: string; icon: string; status: string;
}

export interface Creator {
  id: number; platform: string; platform_uid: string;
  nickname: string; signature: string; avatar_url: string;
  follower_count: number; extra_info: Record<string, any>;
  created_at: string; updated_at: string;
  latest_stats: { total_contents: number; engagement: Record<string, number>; fetched_at: string } | null;
  schedule: Schedule | null;
}

export interface Session {
  id: number; creator_id: number; platform: string;
  trigger_type: string; status: string;
  error_message: string; started_at: string; finished_at: string | null;
  total_contents: number; engagement_total: Record<string, number>;
  delta_from_prev: Record<string, number>;
  creator_nickname: string;
  keyword: string;
}

export interface Content {
  id: number; session_id: number; content_id: string;
  content_type: string; title: string; cover_url: string;
  create_time: string | null; duration_sec: number; url: string;
  tags: string[]; music_title: string;
  likes: number; comments: number; shares: number; plays: number; collects: number;
  extra_data: Record<string, any>;
  platform: string; creator_nickname: string;
}

export interface Schedule {
  id: number; creator_id: number; interval_minutes: number;
  enabled: boolean; last_run_at: string | null; next_run_at: string | null;
  extra_config?: { keyword?: string; max_items?: number } | null;
}

export interface PaginatedResponse<T> {
  total: number; items: T[];
}

// ---- Download ----
export interface DownloadTask {
  id: number; content_snapshot_id: number;
  platform: string; content_id: string; title: string;
  status: 'pending' | 'downloading' | 'completed' | 'failed';
  progress: number; file_path: string; file_size: number;
  error_message: string; retry_count: number;
  created_at: string; started_at: string | null; finished_at: string | null;
  url: string;
}

export interface DownloadStats {
  total: number; pending: number; downloading: number;
  completed: number; failed: number; total_size: number;
}

// --- Hooks ---
export function usePlatforms() {
  return useQuery<Platform[]>({ queryKey: ['platforms'], queryFn: () => client.get('/platforms').then(r => r.data) });
}

export function useCreators(params?: Record<string, any>) {
  return useQuery<PaginatedResponse<Creator>>({
    queryKey: ['creators', params],
    queryFn: () => client.get('/creators', { params }).then(r => r.data),
  });
}

export function useCreator(id: number) {
  return useQuery<Creator>({
    queryKey: ['creators', id],
    queryFn: () => client.get(`/creators/${id}`).then(r => r.data),
  });
}

export function useAddCreator() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { platform: string; platform_uid: string }) => client.post('/creators', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['creators'] }),
  });
}

export function useDeleteCreator() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => client.delete(`/creators/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['creators'] }),
  });
}

export function useTriggerCrawl() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ creatorId, max_items, date_from, date_to }: { creatorId: number; max_items?: number; date_from?: string; date_to?: string }) =>
      client.post(`/creators/${creatorId}/crawl`, { max_items, date_from, date_to }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sessions'] }),
  });
}

export function useSessions(params?: Record<string, any>) {
  return useQuery<PaginatedResponse<Session>>({
    queryKey: ['sessions', params],
    queryFn: () => client.get('/sessions', { params }).then(r => r.data),
  });
}

export function useSessionContents(sessionId: number, params?: Record<string, any>) {
  return useQuery<PaginatedResponse<Content>>({
    queryKey: ['session-contents', sessionId, params],
    queryFn: () => client.get(`/sessions/${sessionId}/contents`, { params }).then(r => r.data),
    enabled: !!sessionId,
  });
}

export function useContents(params?: Record<string, any>) {
  return useQuery<PaginatedResponse<Content>>({
    queryKey: ['contents', params],
    queryFn: () => client.get('/contents', { params }).then(r => r.data),
  });
}

export function useSchedules() {
  return useQuery<Schedule[]>({ queryKey: ['schedules'], queryFn: () => client.get('/schedules').then(r => r.data) });
}

export function useUpsertSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ creatorId, data }: { creatorId: number; data: { interval_minutes: number; enabled: boolean } }) =>
      client.put(`/schedules/${creatorId}`, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['schedules'] }),
  });
}

export function useDeleteSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: number) => client.delete(`/sessions/${sessionId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['sessions'] });
      qc.invalidateQueries({ queryKey: ['creators'] });
    },
  });
}

export function useBatchDeleteSessions() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sessionIds: number[]) => client.delete('/sessions/batch', { data: { session_ids: sessionIds } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['sessions'] });
      qc.invalidateQueries({ queryKey: ['creators'] });
    },
  });
}

export function useClearCreatorSessions() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (creatorId: number) => client.delete(`/sessions/creator/${creatorId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['sessions'] });
      qc.invalidateQueries({ queryKey: ['creators'] });
    },
  });
}

export function useClearAllSessions() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => client.delete('/sessions/cleanup/all'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['sessions'] });
      qc.invalidateQueries({ queryKey: ['creators'] });
    },
  });
}

// ---- Download Hooks ----
export function useDownloadTasks(params?: Record<string, any>) {
  return useQuery<PaginatedResponse<DownloadTask>>({
    queryKey: ['downloads', params],
    queryFn: () => client.get('/downloads', { params }).then(r => r.data),
    refetchInterval: (query) => {
      // 如果有下载中的任务，自动轮询
      const items = query.state.data?.items;
      if (items?.some(t => t.status === 'downloading' || t.status === 'pending')) {
        return 3000;
      }
      return false;
    },
  });
}

export function useDownloadStats() {
  return useQuery<DownloadStats>({
    queryKey: ['download-stats'],
    queryFn: () => client.get('/downloads/stats').then(r => r.data),
    refetchInterval: 5000,
  });
}

export function useBatchDownload() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (contentIds: number[]) => client.post('/downloads/batch', { content_ids: contentIds }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['downloads'] });
      qc.invalidateQueries({ queryKey: ['download-stats'] });
    },
  });
}

export function useRetryDownload() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taskId: number) => client.post(`/downloads/${taskId}/retry`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['downloads'] });
      qc.invalidateQueries({ queryKey: ['download-stats'] });
    },
  });
}

export function useDeleteDownload() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taskId: number) => client.delete(`/downloads/${taskId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['downloads'] });
      qc.invalidateQueries({ queryKey: ['download-stats'] });
    },
  });
}

export function useBatchDeleteDownloads() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taskIds: number[]) => client.delete('/downloads/batch', { data: { task_ids: taskIds } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['downloads'] });
      qc.invalidateQueries({ queryKey: ['download-stats'] });
    },
  });
}

export function useCleanupDownloads() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (status?: string) => client.delete('/downloads/cleanup', { params: status ? { status } : {} }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['downloads'] });
      qc.invalidateQueries({ queryKey: ['download-stats'] });
    },
  });
}

export function useCheckYtdlp() {
  return useQuery<{ available: boolean; version: string; path: string }>({
    queryKey: ['ytdlp-check'],
    queryFn: () => client.get('/downloads/check-ytdlp').then(r => r.data),
    staleTime: 60000, // 1 分钟内不重新检查
  });
}

// ---- Analysis ----
export interface VideoAnalysis {
  id: number;
  download_task_id: number;
  content_snapshot_id: number;
  platform: string;
  content_id: string;
  title: string;
  status: 'pending' | 'extracting' | 'transcribing' | 'analyzing' | 'completed' | 'failed';
  stage: string;
  progress: number;
  asr_provider: string;
  asr_segments: { start: number; end: number; text: string }[];
  transcript: string;
  duration_sec: number;
  language: string;
  script_analysis: string;
  content_summary: string;
  knowledge_summary: string;
  enabled_tasks: string[];
  llm_model: string;
  error_message: string;
  retry_count: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface AnalysisCheck {
  ffmpeg_available: boolean;
  ffmpeg_path: string;
  asr_provider: string;
  asr_ready: boolean;
  asr_message: string;
  llm_ready: boolean;
  llm_message: string;
}

export function useAnalysisList(params?: Record<string, any>) {
  return useQuery({
    queryKey: ['analysis', params],
    queryFn: () => client.get('/analysis', { params }).then(r => r.data),
    refetchInterval: (q: any) => {
      const items: VideoAnalysis[] = q.state?.data?.items || [];
      return items.some(t => ['pending', 'extracting', 'transcribing', 'analyzing'].includes(t.status)) ? 3000 : false;
    },
  });
}

// ---- Analysis Candidates（"选择要分析的视频"候选列表）----
// analysis_status: 'none' | 'pending' | 'analyzing' | 'completed' | 'failed'
// 其中 'none' 表示该视频从未被分析过。
export interface AnalysisCandidate {
  download_task_id: number;
  content_snapshot_id: number;
  platform: string;
  content_id: string;
  title: string;
  file_size: number;
  created_at: string | null;
  analysis_id: number | null;
  analysis_status: 'none' | 'pending' | 'analyzing' | 'completed' | 'failed';
  analysis_progress: number;
}

export function useAnalysisCandidates(params?: Record<string, any>) {
  return useQuery<PaginatedResponse<AnalysisCandidate>>({
    queryKey: ['analysis', 'candidates', params],
    queryFn: () => client.get('/analysis/candidates', { params }).then(r => r.data),
    refetchInterval: (q: any) => {
      const items: AnalysisCandidate[] = q.state?.data?.items || [];
      // 列表中存在分析中的视频时，3s 轮询刷新进度
      return items.some(t => t.analysis_status === 'analyzing') ? 3000 : false;
    },
  });
}

export function useAnalysisDetail(id: number | string) {
  return useQuery({
    queryKey: ['analysis', 'detail', id],
    queryFn: () => client.get(`/analysis/${id}`).then(r => r.data as VideoAnalysis),
    refetchInterval: (q: any) =>
      ['pending', 'extracting', 'transcribing', 'analyzing'].includes(q.state?.data?.status) ? 2000 : false,
  });
}

// LLM 分析项 key（与后端 AnalysisTask 枚举对齐）
export type AnalysisTaskKey = 'summary' | 'script' | 'knowledge';
export const ALL_ANALYSIS_TASKS: AnalysisTaskKey[] = ['summary', 'script', 'knowledge'];

export function useCreateAnalysis() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { download_task_id: number; enabled_tasks?: AnalysisTaskKey[] }) =>
      client.post('/analysis', {
        download_task_id: args.download_task_id,
        enabled_tasks: args.enabled_tasks,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['analysis'] }),
  });
}

export function useBatchCreateAnalysis() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { download_task_ids: number[]; enabled_tasks?: AnalysisTaskKey[] }) =>
      client.post('/analysis/batch', {
        download_task_ids: args.download_task_ids,
        enabled_tasks: args.enabled_tasks,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['analysis'] }),
  });
}

// ---- 跨视频"大总结"（不落库，结果用组件 state 承接，无需 invalidate）----
export interface AnalysisDigestParams {
  analysis_ids: number[];
  task: 'summary' | 'knowledge';
}
export interface AnalysisDigestResult {
  digest: string;
  source_count: number;
  llm_model: string;
}

export function useAnalysisDigest() {
  return useMutation({
    mutationFn: (params: AnalysisDigestParams) =>
      client.post<AnalysisDigestResult>('/analysis/digest', params).then(r => r.data),
  });
}

// ---- 对总结内容多轮对话（不落库，上下文由前端维护）----
export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}
export interface AnalysisChatParams {
  analysis_ids: number[];
  task: 'summary' | 'knowledge';
  messages: ChatMessage[];
}
export interface AnalysisChatResult {
  answer: string;
  llm_model: string;
}

export function useAnalysisChat() {
  return useMutation({
    mutationFn: (params: AnalysisChatParams) =>
      client.post<AnalysisChatResult>('/analysis/chat', params).then(r => r.data),
  });
}

// ---- 批量取分析详情（对比页跨页取数）----
export function useAnalysisBatchDetail(ids: number[]) {
  return useQuery<PaginatedResponse<VideoAnalysis>>({
    queryKey: ['analysis', 'batch', ids],
    queryFn: () =>
      client.post('/analysis/batch-detail', { analysis_ids: ids }).then(r => r.data),
    enabled: ids.length > 0,
  });
}

export function useRetryAnalysis() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, force_asr = false }: { id: number; force_asr?: boolean }) =>
      client.post(`/analysis/${id}/retry`, null, { params: { force_asr } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['analysis'] }),
  });
}

export function useDeleteAnalysis() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => client.delete(`/analysis/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['analysis'] }),
  });
}

export function useAnalysisCheck() {
  return useQuery({ queryKey: ['analysis', 'check'], queryFn: () => client.get('/analysis/check').then(r => r.data as AnalysisCheck) });
}
