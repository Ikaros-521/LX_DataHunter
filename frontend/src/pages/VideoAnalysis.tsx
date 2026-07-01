import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Table, Tag, Button, Space, Progress, Modal, message, Alert, Tooltip,
} from 'antd';
import {
  ReloadOutlined, DeleteOutlined, PlusOutlined,
  CheckCircleOutlined, CloseCircleOutlined, LoadingOutlined, ClockCircleOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import {
  useAnalysisList, useAnalysisCheck, useCreateAnalysis,
  useRetryAnalysis, useDeleteAnalysis, useDownloadTasks,
  type VideoAnalysis, type DownloadTask,
} from '../api/queries';
import PlatformTag from '../components/PlatformTag';
import useAnalysisProgress from '../hooks/useAnalysisProgress';

const STATUS_CFG: Record<string, { color: string; text: string; icon: React.ReactNode }> = {
  pending: { color: 'default', text: '排队中', icon: <ClockCircleOutlined /> },
  extracting: { color: 'processing', text: '抽音频', icon: <LoadingOutlined /> },
  transcribing: { color: 'processing', text: '识别中', icon: <LoadingOutlined /> },
  analyzing: { color: 'processing', text: '分析中', icon: <LoadingOutlined /> },
  completed: { color: 'success', text: '已完成', icon: <CheckCircleOutlined /> },
  failed: { color: 'error', text: '失败', icon: <CloseCircleOutlined /> },
};

export default function VideoAnalysisPage() {
  useAnalysisProgress();
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [pickerOpen, setPickerOpen] = useState(false);

  const { data, isLoading } = useAnalysisList({ page, page_size: 20 });
  const { data: check } = useAnalysisCheck();
  const createMut = useCreateAnalysis();
  const retryMut = useRetryAnalysis();
  const deleteMut = useDeleteAnalysis();
  const { data: downloadsData } = useDownloadTasks({ status: 'completed', page: 1, page_size: 200 });

  const analyzedTaskIds = new Set((data?.items || []).map((a: VideoAnalysis) => a.download_task_id));
  const candidates: DownloadTask[] = (downloadsData?.items || []).filter(d => !analyzedTaskIds.has(d.id));

  const handleCreate = (downloadTaskId: number) => {
    createMut.mutate(downloadTaskId, {
      onSuccess: (res: any) => {
        message.success('已创建分析任务');
        setPickerOpen(false);
        navigate(`/analysis/${res.data.id}`);
      },
    });
  };

  const columns = [
    { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true,
      render: (t: string) => t || '(无标题)' },
    { title: '平台', dataIndex: 'platform', key: 'platform', width: 80,
      render: (p: string) => <PlatformTag platform={p} /> },
    { title: '状态', dataIndex: 'status', key: 'status', width: 110,
      render: (s: string, r: VideoAnalysis) => {
        const cfg = STATUS_CFG[s] || { color: 'default', text: s, icon: null };
        return <Tooltip title={r.stage || ''}><Tag icon={cfg.icon} color={cfg.color}>{cfg.text}</Tag></Tooltip>;
      } },
    { title: '进度', dataIndex: 'progress', key: 'progress', width: 150,
      render: (p: number, r: VideoAnalysis) => r.status === 'completed'
        ? <Tag color="success">100%</Tag>
        : r.status === 'failed' ? <Tag color="error">失败</Tag>
        : <Progress percent={Math.round(p)} size="small" status="active" /> },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 160,
      render: (t: string) => t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-' },
    { title: '操作', key: 'actions', width: 180,
      render: (_: any, r: VideoAnalysis) => (
        <Space size="small">
          <Button size="small" onClick={() => navigate(`/analysis/${r.id}`)}>查看</Button>
          {r.status === 'failed' && (
            <Tooltip title="重试">
              <Button size="small" icon={<ReloadOutlined />}
                onClick={() => retryMut.mutate({ id: r.id }, { onSuccess: () => message.success('已重新入队') })} />
            </Tooltip>
          )}
          <Button size="small" danger icon={<DeleteOutlined />}
            onClick={() => deleteMut.mutate(r.id, { onSuccess: () => message.success('已删除') })} />
        </Space>
      ) },
  ];

  return (
    <div>
      {check && (!check.ffmpeg_available || !check.asr_ready || !check.llm_ready) && (
        <Alert type="warning" showIcon style={{ marginBottom: 16 }}
          message="分析功能未就绪"
          description={`ffmpeg: ${check.ffmpeg_available ? '✓' : '✗ 未安装'} | ASR(${check.asr_provider}): ${check.asr_ready ? '✓' : '✗ ' + check.asr_message} | LLM: ${check.llm_ready ? '✓' : '✗ ' + check.llm_message}`} />
      )}

      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setPickerOpen(true)}>新建分析</Button>
        <Tag>视频分析</Tag>
      </Space>

      <Table dataSource={data?.items || []} columns={columns} rowKey="id" loading={isLoading} size="small"
        pagination={data?.total ? { current: page, total: data.total, pageSize: 20,
          onChange: p => setPage(p) } : false}
        locale={{ emptyText: '暂无分析任务' }} />

      <Modal open={pickerOpen} title="选择要分析的视频" onCancel={() => setPickerOpen(false)} footer={null} width={640}>
        <Table dataSource={candidates} rowKey="id" size="small" pagination={false}
          locale={{ emptyText: '没有"已下载完成且未分析"的视频' }}
          columns={[
            { title: '标题', dataIndex: 'title', ellipsis: true, render: (t: string) => t || '(无标题)' },
            { title: '平台', dataIndex: 'platform', width: 80, render: (p: string) => <PlatformTag platform={p} /> },
            { title: '操作', width: 90, render: (_: any, d: DownloadTask) =>
              <Button size="small" type="primary" onClick={() => handleCreate(d.id)}>分析</Button> },
          ]} />
      </Modal>
    </div>
  );
}
