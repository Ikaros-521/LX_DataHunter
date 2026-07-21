import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Table, Tag, Button, Space, Progress, Modal, message, Alert, Tooltip,
  Input, Select, Popconfirm, Checkbox,
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
  ALL_ANALYSIS_TASKS, type AnalysisTaskKey,
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

// 候选弹窗里"分析项"勾选的展示配置（label + 中文文案）
const TASK_OPTIONS: { label: string; value: AnalysisTaskKey }[] = [
  { label: '内容总结', value: 'summary' },
  { label: '脚本与制作手法', value: 'script' },
  { label: '视频知识总结', value: 'knowledge' },
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
  // 本次创建/批量分析启用的 LLM 分析项（默认全选）
  const [enabledTasks, setEnabledTasks] = useState<AnalysisTaskKey[]>([...ALL_ANALYSIS_TASKS]);

  // 对比查看：列表主表多选的 analysis id（跳转到对比独立页）
  const [compareIds, setCompareIds] = useState<number[]>([]);

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
    createMut.mutate({ download_task_id: downloadTaskId, enabled_tasks: enabledTasks }, {
      onSuccess: (res: any) => {
        message.success('已创建分析任务');
        setPickerOpen(false);
        navigate(`/analysis/${res.data.id}`);
      },
    });
  };

  const handleBatchCreate = () => {
    if (!selectedTaskIds.length) return;
    batchCreateMut.mutate({ download_task_ids: selectedTaskIds, enabled_tasks: enabledTasks }, {
      onSuccess: (res: any) => {
        const d = res?.data || {};
        message.success(d.message || `已入队 ${selectedTaskIds.length} 个分析任务`);
        setSelectedTaskIds([]);
        setPickerOpen(false);
        navigate('/analysis');
      },
    });
  };

  // 打开对比独立页：把选中的 analysis id 通过 URL 传递，对比页用 batch-detail 取数
  const openCompare = () => {
    if (compareIds.length < 2) {
      message.warning('请至少选择 2 条已完成的分析');
      return;
    }
    navigate(`/analysis/compare?ids=${compareIds.join(',')}`);
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
        {compareIds.length > 0 && (
          <Button icon={<EyeOutlined />} onClick={openCompare}>
            对比查看 {compareIds.length}
          </Button>
        )}
        <Tag>视频分析</Tag>
      </Space>

      <Table dataSource={data?.items || []} columns={columns} rowKey="id" loading={isLoading} size="small"
        rowSelection={{
          selectedRowKeys: compareIds,
          onChange: (keys) => setCompareIds(keys as number[]),
          // 仅已完成的分析有总结可对比
          getCheckboxProps: (r: VideoAnalysis) => ({
            disabled: r.status !== 'completed',
          }),
        }}
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
        <div style={{ marginBottom: 12 }}>
          <span style={{ marginRight: 8, color: '#666' }}>分析项：</span>
          <Checkbox.Group
            options={TASK_OPTIONS}
            value={enabledTasks}
            onChange={(v) => setEnabledTasks(v as AnalysisTaskKey[])}
          />
          {enabledTasks.length === 0 && (
            <span style={{ marginLeft: 8, color: '#ff4d4f', fontSize: 12 }}>
              至少需勾选一项
            </span>
          )}
        </div>
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
