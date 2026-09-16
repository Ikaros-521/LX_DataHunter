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

// ---- 竞品调研 (Competitor Research) ----
export interface Competitor {
  id: number; name: string; platform: string; platform_uid: string;
  homepage_url: string; nickname: string; note: string; enabled: boolean;
  follower_count: number; last_researched_at: string | null;
  extra_info: Record<string, any>; created_at: string; updated_at: string;
}

export interface DiscoveredCompetitor {
  platform: string; platform_uid: string; nickname: string;
  signature: string; follower_count: number; video_hits: number;
  latest_pub_time: string; total_plays: number;
  sample_titles: string[]; homepage_url: string;
}

export interface ResearchKeyword {
  id: number; keyword: string; platform: string; note: string;
  enabled: boolean; last_researched_at: string | null;
  extra_info: Record<string, any>; created_at: string; updated_at: string;
}

export interface ResearchTask {
  id: number; title: string; mode: string; status: string; stage: string; progress: number;
  competitor_ids: number[]; keyword_ids: number[]; competitor_count: number;
  params: Record<string, any>; market_overview: string; markdown_path: string;
  trigger_type: string; error_message: string;
  created_at: string; started_at: string | null; finished_at: string | null;
}

export interface ResearchSchedule {
  id: number; interval_minutes: number; enabled: boolean;
  last_run_at: string | null; next_run_at: string | null;
  params: Record<string, any>;
}

export function useCompetitors(params?: Record<string, any>) {
  return useQuery<PaginatedResponse<Competitor>>({
    queryKey: ['competitors', params],
    queryFn: () => client.get('/competitors', { params }).then(r => r.data),
  });
}

export function useAddCompetitor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { platform: string; platform_uid: string; name?: string; note?: string }) =>
      client.post('/competitors', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['competitors'] }),
  });
}

export function useUpdateCompetitor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: number; name?: string; note?: string; enabled?: boolean }) =>
      client.put(`/competitors/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['competitors'] }),
  });
}

export function useDeleteCompetitor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => client.delete(`/competitors/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['competitors'] }),
  });
}

export function useDiscoverCompetitors() {
  return useMutation({
    mutationFn: (body: { platform: string; keyword: string; max_items?: number; max_pages?: number }) =>
      client.post('/competitors/discover', body, { timeout: 600000 }).then(
        r => r.data as { platform: string; keyword: string; candidates: DiscoveredCompetitor[]; message: string }),
  });
}

const ACTIVE_RESEARCH_STATUSES = ['pending', 'collecting', 'crawling_comments', 'analyzing'];

export function useResearchList() {
  return useQuery<PaginatedResponse<ResearchTask>>({
    queryKey: ['research'],
    queryFn: () => client.get('/research', { params: { page_size: 50 } }).then(r => r.data),
    // 有进行中的任务时 2s 轮询进度
    refetchInterval: (query) =>
      query.state.data?.items.some(t => ACTIVE_RESEARCH_STATUSES.includes(t.status)) ? 2000 : false,
  });
}

export function useResearchDetail(id: number | null) {
  return useQuery<ResearchTask>({
    queryKey: ['research', id],
    queryFn: () => client.get(`/research/${id}`).then(r => r.data),
    enabled: id != null,
    refetchInterval: (query) =>
      query.state.data && ACTIVE_RESEARCH_STATUSES.includes(query.state.data.status) ? 2000 : false,
  });
}

export function useResearchKeywords() {
  return useQuery<PaginatedResponse<ResearchKeyword>>({
    queryKey: ['research', 'keywords'],
    queryFn: () => client.get('/research/keywords', { params: { page_size: 100 } }).then(r => r.data),
  });
}

export function useAddResearchKeyword() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { keyword: string; platform: string; note?: string }) =>
      client.post('/research/keywords', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['research', 'keywords'] }),
  });
}

export function useUpdateResearchKeyword() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: number; note?: string; enabled?: boolean }) =>
      client.put(`/research/keywords/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['research', 'keywords'] }),
  });
}

export function useDeleteResearchKeyword() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => client.delete(`/research/keywords/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['research', 'keywords'] }),
  });
}

export function useCreateResearch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      title?: string; mode?: string;
      competitor_ids?: number[]; keyword_ids?: number[];
      date_from?: string | null; date_to?: string | null;
      max_videos_per_competitor?: number; top_videos_for_comments?: number;
      max_comments_per_video?: number;
      max_videos_per_keyword?: number; max_search_pages?: number;
      crawl_danmaku?: boolean; max_danmakus_per_video?: number;
    }) => client.post('/research', body).then(r => r.data as ResearchTask),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['research'] }),
  });
}

export function useDeleteResearch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => client.delete(`/research/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['research'] }),
  });
}

export function useResearchSchedule() {
  return useQuery<ResearchSchedule>({
    queryKey: ['research', 'schedule'],
    queryFn: () => client.get('/research/schedule').then(r => r.data),
  });
}

export function useUpdateResearchSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { interval_minutes: number; enabled: boolean; params?: Record<string, any> }) =>
      client.put('/research/schedule', body).then(r => r.data as ResearchSchedule),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['research', 'schedule'] }),
  });
}

/** 拉取报告 markdown 原文（非 JSON）。 */
export async function fetchReportMarkdown(id: number): Promise<string> {
  const resp = await client.get(`/research/${id}/file`, { responseType: 'text', transformResponse: (x) => x });
  return resp.data as string;
}

export interface ResearchDanmakuItem {
  id: number;
  competitor_id: number;
  content_id: string;
  danmaku_id: string;
  text: string;
  send_time: string | null;
  dm_time: number;
  mode: number;
  font_size: number;
  color: string;
  user_hash: string;
  pool: number;
}

export interface ResearchDanmakuList {
  total: number;
  contents: { content_id: string; title: string; count: number }[];
  items: ResearchDanmakuItem[];
}

/** 调研任务的原始弹幕（分页，可按视频筛选）。 */
export function useResearchDanmakus(
  researchId: number | null,
  params: { content_id?: string; page: number; page_size?: number },
  enabled: boolean,
) {
  return useQuery<ResearchDanmakuList>({
    queryKey: ['research', researchId, 'danmakus', params],
    enabled: enabled && researchId != null,
    queryFn: () => client.get(`/research/${researchId}/danmakus`, { params })
      .then(r => r.data as ResearchDanmakuList),
  });
}
