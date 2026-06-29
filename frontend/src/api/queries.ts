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
