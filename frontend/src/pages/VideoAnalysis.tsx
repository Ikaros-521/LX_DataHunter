import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Table, Tag, Button, Space, Progress, Modal, message, Alert, Tooltip,
  Input, Select, Popconfirm,
} from 'antd';
import {
  ReloadOutlined, DeleteOutlined, PlusOutlined,
  CheckCircleOutlined, CloseCircleOutlined, LoadingOutlined, ClockCircleOutlined,
  EyeOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import {
  useAnalysisList, useAnalysisCheck, useCreateAnalysis,
  useRetryAnalysis, useDeleteAnalysis, useAnalysisCandidates,
  useBatchCreateAnalysis,
  type VideoAnalysis, type AnalysisCandidate,
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

// 候选列表里"分析状态"的展示配置（聚合态：未分析/分析中/已完成/失败）。
const CAND_STATUS_CFG: Record<string, { color: string; text: string; icon: React.ReactNode }> = {
  none: { color: 'default', text: '未分析', icon: <ClockCircleOutlined /> },
  analyzing: { color: 'processing', text: '分析中', icon: <LoadingOutlined /> },
  completed: { color: 'success', text: '已完成', icon: <CheckCircleOutlined /> },
  failed: { color: 'error', text: '失败', icon: <CloseCircleOutlined /> },
  pending: { color: 'default', text: '排队中', icon: <ClockCircleOutlined /> },
};

// 候选弹窗里的分析状态筛选项
const CAND_STATUS_FILTERS = [
  { label: '未分析', value: 'none' },
  { label: '分析中', value: 'analyzing' },
  { label: '已完成', value: 'completed' },
  { label: '失败', value: 'failed' },
  { label: '全部', value: 'all' },
];

const PLATFORM_OPTIONS = [
  { label: '抖音', value: 'douyin' },
  { label: 'B站', value: 'bilibili' },
];

export default function VideoAnalysisPage() {
  useAnalysisProgress();
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [pickerOpen, setPickerOpen] = useState(false);

  // 候选弹窗的筛选与分页 state
  const [pickerSearch, setPickerSearch] = useState('');
  const [pickerPlatform, setPickerPlatform] = useState<string | undefined>(undefined);
  const [pickerStatus, setPickerStatus] = useState<string>('none');
  const [pickerPage, setPickerPage] = useState(1);
  const [selectedTaskIds, setSelectedTaskIds] = useState<number[]>([]);

  const { data, isLoading } = useAnalysisList({ page, page_size: 20 });
  const { data: check } = useAnalysisCheck();
  const createMut = useCreateAnalysis();
  const retryMut = useRetryAnalysis();
  const deleteMut = useDeleteAnalysis();
  const batchCreateMut = useBatchCreateAnalysis();

  // 候选列表：已下载完成且有本地文件的视频，带分析状态字段
  const { data: candidatesData, isLoading: candidatesLoading } = useAnalysisCandidates({
    search: pickerSearch || undefined,
    platform: pickerPlatform,
    analysis_status: pickerStatus,
    page: pickerPage,
    page_size: 10,
  });

  // 筛选/搜索变更时重置页码和选择
  const resetPickerPage = () => { setPickerPage(1); setSelectedTaskIds([]); };

  const handleCreate = (downloadTaskId: number) => {
    createMut.mutate(downloadTaskId, {
      onSuccess: (res: any) => {
        message.success('已创建分析任务');
        setPickerOpen(false);
        navigate(`/analysis/${res.data.id}`);
      },
    });
  };

  const handleBatchCreate = () => {
    if (!selectedTaskIds.length) return;
    batchCreateMut.mutate(selectedTaskIds, {
      onSuccess: (res: any) => {
        const d = res?.data || {};
        message.success(d.message || `已入队 ${selectedTaskIds.length} 个分析任务`);
        setSelectedTaskIds([]);
        setPickerOpen(false);
        navigate('/analysis');
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

      <Modal open={pickerOpen} title="选择要分析的视频" onCancel={() => setPickerOpen(false)} footer={null} width={860}
        destroyOnClose>
        <Space wrap style={{ marginBottom: 12, width: '100%' }}>
          <Input.Search
            allowClear
            placeholder="搜索视频标题"
            value={pickerSearch}
            onChange={(e) => { setPickerSearch(e.target.value); resetPickerPage(); }}
            onSearch={() => resetPickerPage()}
            style={{ width: 240 }}
          />
          <Select
            allowClear
            placeholder="平台"
            value={pickerPlatform}
            onChange={(v) => { setPickerPlatform(v || undefined); resetPickerPage(); }}
            style={{ width: 110 }}
            options={PLATFORM_OPTIONS}
          />
          <Select
            value={pickerStatus}
            onChange={(v) => { setPickerStatus(v); resetPickerPage(); }}
            style={{ width: 110 }}
            options={CAND_STATUS_FILTERS}
          />
          {selectedTaskIds.length > 0 && (
            <Popconfirm
              title={`批量分析选中的 ${selectedTaskIds.length} 个视频？`}
              description="将统一入队，由后台按并发限制自动排队执行。"
              onConfirm={handleBatchCreate}
            >
              <Button type="primary" icon={<ThunderboltOutlined />}
                loading={batchCreateMut.isPending}>
                批量分析 {selectedTaskIds.length}
              </Button>
            </Popconfirm>
          )}
        </Space>
        <Table
          dataSource={candidatesData?.items || []}
          rowKey="download_task_id"
          size="small"
          loading={candidatesLoading}
          rowSelection={{
            selectedRowKeys: selectedTaskIds,
            onChange: (keys) => setSelectedTaskIds(keys as number[]),
            // 仅未分析的视频可勾选；已分析/分析中/失败不参与批量（避免误重置结果）
            getCheckboxProps: (r: AnalysisCandidate) => ({
              disabled: r.analysis_status !== 'none',
            }),
          }}
          pagination={candidatesData?.total ? {
            current: pickerPage,
            total: candidatesData.total,
            pageSize: 10,
            onChange: (p) => setPickerPage(p),
            showTotal: (t) => `共 ${t} 条`,
            size: 'small',
          } : false}
          locale={{ emptyText: '没有符合条件的视频' }}
          columns={[
            { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true,
              render: (t: string) => t || '(无标题)' },
            { title: '平台', dataIndex: 'platform', key: 'platform', width: 80,
              render: (p: string) => <PlatformTag platform={p} /> },
            { title: '分析状态', dataIndex: 'analysis_status', key: 'analysis_status', width: 110,
              render: (s: string) => {
                const cfg = CAND_STATUS_CFG[s] || { color: 'default', text: s, icon: null };
                return <Tag icon={cfg.icon} color={cfg.color}>{cfg.text}</Tag>;
              } },
            { title: '进度', key: 'progress', width: 140,
              render: (_: any, r: AnalysisCandidate) => {
                if (r.analysis_status === 'none') return <span style={{ color: '#999' }}>-</span>;
                if (r.analysis_status === 'completed') return <Tag color="success">100%</Tag>;
                if (r.analysis_status === 'failed') return <Tag color="error">失败</Tag>;
                return <Progress percent={Math.round(r.analysis_progress)} size="small" status="active" />;
              } },
            { title: '操作', key: 'actions', width: 110,
              render: (_: any, r: AnalysisCandidate) => {
                if (r.analysis_status === 'none') {
                  return (
                    <Button size="small" type="primary"
                      loading={createMut.isPending}
                      onClick={() => handleCreate(r.download_task_id)}>
                      分析
                    </Button>
                  );
                }
                if (r.analysis_status === 'analyzing' || r.analysis_status === 'pending') {
                  return <Button size="small" disabled>分析中</Button>;
                }
                // completed / failed：可查看详情（失败也可进详情看错误信息）
                return (
                  <Button size="small" icon={<EyeOutlined />}
                    onClick={() => {
                      setPickerOpen(false);
                      navigate(`/analysis/${r.analysis_id}`);
                    }}>
                    查看
                  </Button>
                );
              } },
          ]} />
      </Modal>
    </div>
  );
}
