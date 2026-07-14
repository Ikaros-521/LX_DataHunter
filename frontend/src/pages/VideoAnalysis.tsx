import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Table, Tag, Button, Space, Progress, Modal, message, Alert, Tooltip,
  Input, Select, Popconfirm, Checkbox, Drawer, Tabs, Card, Spin, Typography,
} from 'antd';
import {
  ReloadOutlined, DeleteOutlined, PlusOutlined,
  CheckCircleOutlined, CloseCircleOutlined, LoadingOutlined, ClockCircleOutlined,
  EyeOutlined, ThunderboltOutlined, CopyOutlined, DownloadOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import dayjs from 'dayjs';
import {
  useAnalysisList, useAnalysisCheck, useCreateAnalysis,
  useRetryAnalysis, useDeleteAnalysis, useAnalysisCandidates,
  useBatchCreateAnalysis, useAnalysisDigest,
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

  // 对比查看：列表主表多选的 analysis id
  const [compareIds, setCompareIds] = useState<number[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);
  // 大总结：聚合维度 + 生成结果（不落库，仅本地 state）
  const [digestTask, setDigestTask] = useState<'summary' | 'knowledge'>('summary');
  const [digestText, setDigestText] = useState('');

  const { data, isLoading } = useAnalysisList({ page, page_size: 20 });
  const { data: check } = useAnalysisCheck();
  const createMut = useCreateAnalysis();
  const retryMut = useRetryAnalysis();
  const deleteMut = useDeleteAnalysis();
  const batchCreateMut = useBatchCreateAnalysis();
  const digestMut = useAnalysisDigest();

  // 当前对比 Drawer 要展示的分析记录（从列表数据里取，已含完整字段）
  const compareItems: VideoAnalysis[] = (data?.items || []).filter(a => compareIds.includes(a.id));

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

  // 打开对比 Drawer：固定当前选中集合，避免列表翻页后 items 取不到
  const openCompare = () => {
    if (compareIds.length < 2) {
      message.warning('请至少选择 2 条已完成的分析');
      return;
    }
    setDigestText('');
    setCompareOpen(true);
  };

  // 生成跨视频大总结（不落库，结果存本地 state）
  const handleDigest = () => {
    if (compareIds.length < 2) return;
    digestMut.mutate(
      { analysis_ids: compareIds, task: digestTask },
      {
        onSuccess: (data) => {
          setDigestText(data.digest);
          message.success(`已生成大总结（聚合 ${data.source_count} 条）`);
        },
        onError: () => message.error('大总结生成失败'),
      },
    );
  };

  const handleCopyDigest = () => {
    if (!digestText) return;
    navigator.clipboard.writeText(digestText).then(
      () => message.success('已复制到剪贴板'),
      () => message.error('复制失败'),
    );
  };

  const handleDownloadDigest = () => {
    if (!digestText) return;
    const blob = new Blob([digestText], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `digest_${digestTask}_${Date.now()}.md`;
    a.click();
    URL.revokeObjectURL(url);
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

      {/* 对比查看 Drawer：并排展示多条已完成分析，并支持生成跨视频大总结 */}
      <Drawer
        title={`对比查看（${compareItems.length} 条）`}
        open={compareOpen}
        onClose={() => setCompareOpen(false)}
        width={760}
        destroyOnClose
      >
        {/* 大总结操作区 */}
        <Card size="small" title="跨视频大总结" style={{ marginBottom: 16 }}>
          <Space wrap style={{ marginBottom: 8 }}>
            <Select
              value={digestTask}
              onChange={(v) => setDigestTask(v)}
              style={{ width: 140 }}
              options={[
                { label: '基于内容总结', value: 'summary' },
                { label: '基于知识总结', value: 'knowledge' },
              ]}
            />
            <Button type="primary" icon={<ThunderboltOutlined />}
              loading={digestMut.isPending}
              onClick={handleDigest}>
              生成大总结
            </Button>
            {digestText && (
              <>
                <Button icon={<CopyOutlined />} onClick={handleCopyDigest}>复制</Button>
                <Button icon={<DownloadOutlined />} onClick={handleDownloadDigest}>下载</Button>
              </>
            )}
          </Space>
          {digestMut.isPending ? (
            <div style={{ textAlign: 'center', padding: 24 }}>
              <Spin tip="正在调用 LLM 生成大总结，请稍候（通常 10-30 秒）…" />
            </div>
          ) : digestText ? (
            <Typography>
              <ReactMarkdown>{digestText}</ReactMarkdown>
            </Typography>
          ) : (
            <div style={{ color: '#999', fontSize: 12 }}>
              选择聚合维度后点"生成大总结"，把多条视频的已有总结整合成一份综合报告（不保存，刷新即丢）。
            </div>
          )}
        </Card>

        {/* 视频卡片纵列：每条分析一张卡片，卡片内 Tab 切换分析项 */}
        {compareItems.map((a) => {
          const tabs = [
            ...(a.enabled_tasks?.includes('summary') ? [{
              key: 'summary', label: '内容总结',
              children: <ReactMarkdown>{a.content_summary || '（空）'}</ReactMarkdown>,
            }] : []),
            ...(a.enabled_tasks?.includes('script') ? [{
              key: 'script', label: '脚本与制作手法',
              children: <ReactMarkdown>{a.script_analysis || '（空）'}</ReactMarkdown>,
            }] : []),
            ...(a.enabled_tasks?.includes('knowledge') ? [{
              key: 'knowledge', label: '视频知识总结',
              children: <ReactMarkdown>{a.knowledge_summary || '（空）'}</ReactMarkdown>,
            }] : []),
          ];
          return (
            <Card
              key={a.id}
              size="small"
              style={{ marginBottom: 12 }}
              title={
                <Space size="small">
                  <span style={{ fontWeight: 500 }}>{a.title || '(无标题)'}</span>
                  <PlatformTag platform={a.platform} />
                </Space>
              }
              extra={
                <Button size="small" type="link" icon={<EyeOutlined />}
                  onClick={() => { setCompareOpen(false); navigate(`/analysis/${a.id}`); }}>
                  详情
                </Button>
              }
            >
              {tabs.length > 0 ? (
                <Tabs size="small" items={tabs} />
              ) : (
                <span style={{ color: '#999' }}>（无已生成的分析项）</span>
              )}
            </Card>
          );
        })}
      </Drawer>
    </div>
  );
}
