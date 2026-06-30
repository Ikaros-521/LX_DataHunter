import { useState } from 'react';
import {
  Row, Col, Table, Tag, Button, Select, Space, Progress, Popconfirm,
  message, Alert, Tooltip, Dropdown, Modal,
} from 'antd';
import {
  DownloadOutlined, DeleteOutlined, ReloadOutlined,
  CheckCircleOutlined, ClockCircleOutlined, CloseCircleOutlined,
  LoadingOutlined, ClearOutlined, CloudDownloadOutlined,
  WarningOutlined, PlayCircleOutlined, EyeOutlined,
} from '@ant-design/icons';
import StatCard from '../components/StatCard';
import PlatformTag from '../components/PlatformTag';
import useDownloadProgress from '../hooks/useDownloadProgress';
import {
  useDownloadTasks, useDownloadStats,
  useRetryDownload, useDeleteDownload, useBatchDeleteDownloads,
  useCleanupDownloads, useCheckYtdlp,
} from '../api/queries';
import type { DownloadTask } from '../api/queries';
import dayjs from 'dayjs';

const STATUS_FILTERS = [
  { label: '全部', value: '' },
  { label: '排队中', value: 'pending' },
  { label: '下载中', value: 'downloading' },
  { label: '已完成', value: 'completed' },
  { label: '失败', value: 'failed' },
];

function formatFileSize(bytes: number): string {
  if (bytes === 0) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

const statusConfig: Record<string, { color: string; text: string; icon: React.ReactNode }> = {
  pending: { color: 'default', text: '排队中', icon: <ClockCircleOutlined /> },
  downloading: { color: 'processing', text: '下载中', icon: <LoadingOutlined /> },
  completed: { color: 'success', text: '已完成', icon: <CheckCircleOutlined /> },
  failed: { color: 'error', text: '失败', icon: <CloseCircleOutlined /> },
};

export default function DownloadManager() {
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [platformFilter, setPlatformFilter] = useState<string | undefined>();
  const [page, setPage] = useState(1);
  const [selectedRowKeys, setSelectedRowKeys] = useState<number[]>([]);
  const [previewTask, setPreviewTask] = useState<DownloadTask | null>(null);

  // 已下载文件的访问 URL（同源相对路径）。download=true 触发浏览器"另存为"。
  const fileUrl = (id: number, download = false) =>
    `/api/v1/downloads/${id}/file${download ? '?download=true' : ''}`;

  // 订阅 WebSocket 进度
  useDownloadProgress();

  // 数据
  const { data, isLoading } = useDownloadTasks({
    status: statusFilter || undefined,
    platform: platformFilter,
    page,
    page_size: 20,
  });
  const { data: stats, isLoading: statsLoading } = useDownloadStats();
  const { data: ytdlpInfo } = useCheckYtdlp();

  // 操作
  const retryMut = useRetryDownload();
  const deleteMut = useDeleteDownload();
  const batchDeleteMut = useBatchDeleteDownloads();
  const cleanupMut = useCleanupDownloads();

  // 筛选变更时重置页码和选中
  const handleFilterChange = (setStatus: (v: string) => void, value: string) => {
    setStatus(value);
    setPage(1);
    setSelectedRowKeys([]);
  };

  const handleRetry = (taskId: number) => {
    retryMut.mutate(taskId, {
      onSuccess: () => message.success('已重新加入下载队列'),
    });
  };

  const handleDelete = (taskId: number) => {
    deleteMut.mutate(taskId, {
      onSuccess: () => message.success('已删除'),
    });
  };

  const handleBatchDelete = () => {
    if (!selectedRowKeys.length) return;
    batchDeleteMut.mutate(selectedRowKeys, {
      onSuccess: () => {
        message.success(`已删除 ${selectedRowKeys.length} 个任务`);
        setSelectedRowKeys([]);
      },
    });
  };

  const handleCleanup = (status?: string) => {
    cleanupMut.mutate(status, {
      onSuccess: (res: any) => message.success(res.data?.message || '清理完成'),
    });
  };

  const handleBatchRetryFailed = () => {
    const failedIds = (data?.items || [])
      .filter(t => t.status === 'failed')
      .map(t => t.id);
    if (!failedIds.length) {
      message.warning('没有失败的任务');
      return;
    }
    Promise.all(failedIds.map(id => retryMut.mutateAsync(id))).then(() => {
      message.success(`已重试 ${failedIds.length} 个失败任务`);
    });
  };

  const columns = [
    {
      title: '标题', dataIndex: 'title', key: 'title', ellipsis: true,
      render: (t: string, record: DownloadTask) => {
        const title = t || '(无标题)';
        // 悬停标题可看到本地文件位置
        return record.file_path ? (
          <Tooltip title={`📁 ${record.file_path}`}><span>{title}</span></Tooltip>
        ) : <span>{title}</span>;
      },
    },
    {
      title: '平台', dataIndex: 'platform', key: 'platform', width: 80,
      render: (p: string) => <PlatformTag platform={p} />,
    },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 100,
      render: (s: string) => {
        const cfg = statusConfig[s] || { color: 'default', text: s, icon: null };
        return (
          <Tag icon={cfg.icon} color={cfg.color}>
            {cfg.text}
          </Tag>
        );
      },
    },
    {
      title: '进度', dataIndex: 'progress', key: 'progress', width: 150,
      render: (p: number, record: DownloadTask) => {
        if (record.status === 'completed') return <Tag color="success">100%</Tag>;
        if (record.status === 'failed') return <Tag color="error">失败</Tag>;
        return (
          <Progress
            percent={Math.round(p)}
            size="small"
            status={record.status === 'downloading' ? 'active' : 'normal'}
          />
        );
      },
    },
    {
      title: '文件大小', dataIndex: 'file_size', key: 'size', width: 100,
      render: (s: number) => formatFileSize(s),
    },
    {
      title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 140,
      render: (t: string) => t ? dayjs(t).format('YYYY-MM-DD HH:mm:ss') : '-',
    },
    {
      title: '操作', key: 'actions', width: 200,
      render: (_: any, record: DownloadTask) => (
        <Space size="small">
          {record.status === 'failed' && (
            <Tooltip title="重试">
              <Button size="small" icon={<ReloadOutlined />} onClick={() => handleRetry(record.id)} />
            </Tooltip>
          )}
          {record.status === 'completed' && (
            <>
              <Tooltip title="预览">
                <Button
                  size="small"
                  icon={<EyeOutlined />}
                  onClick={() => setPreviewTask(record)}
                />
              </Tooltip>
              <Tooltip title="另存为">
                <Button
                  size="small"
                  icon={<DownloadOutlined />}
                  href={fileUrl(record.id, true)}
                />
              </Tooltip>
              {record.url && (
                <Tooltip title="打开原文">
                  <Button
                    size="small"
                    icon={<PlayCircleOutlined />}
                    onClick={() => window.open(record.url, '_blank')}
                  />
                </Tooltip>
              )}
            </>
          )}
          <Popconfirm
            title="确认删除?"
            description={record.status === 'completed' ? '同时删除本地文件' : undefined}
            onConfirm={() => handleDelete(record.id)}
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // 动态总大小
  const totalSizeStr = formatFileSize(stats?.total_size || 0);

  return (
    <div>
      {/* yt-dlp 状态 */}
      {ytdlpInfo && !ytdlpInfo.available && (
        <Alert
          type="warning"
          showIcon
          icon={<WarningOutlined />}
          style={{ marginBottom: 16 }}
          message="yt-dlp 未安装"
          description={
            <span>
              视频下载功能需要 yt-dlp。请安装：{' '}
              <code>pip install yt-dlp</code>
              {ytdlpInfo.version && ` (当前: ${ytdlpInfo.version})`}
            </span>
          }
        />
      )}

      {/* 统计卡片 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={6}>
          <StatCard title="总任务" value={stats?.total || 0} icon={<DownloadOutlined />} color="#1677ff" loading={statsLoading} />
        </Col>
        <Col xs={12} sm={6}>
          <StatCard title="下载中" value={stats?.downloading || 0} icon={<CloudDownloadOutlined />} color="#1890ff" loading={statsLoading} />
        </Col>
        <Col xs={12} sm={6}>
          <StatCard title="已完成" value={stats?.completed || 0} suffix={totalSizeStr ? ` (${totalSizeStr})` : ''} icon={<CheckCircleOutlined />} color="#52c41a" loading={statsLoading} />
        </Col>
        <Col xs={12} sm={6}>
          <StatCard title="失败" value={stats?.failed || 0} icon={<CloseCircleOutlined />} color="#ff4d4f" loading={statsLoading} />
        </Col>
      </Row>

      {/* 筛选栏 */}
      <Space wrap style={{ marginBottom: 16, width: '100%' }}>
        <Select
          value={statusFilter}
          onChange={(v) => handleFilterChange(setStatusFilter, v)}
          style={{ width: 100 }}
          options={STATUS_FILTERS}
        />
        <Select
          allowClear
          placeholder="平台"
          value={platformFilter}
          onChange={(v) => {
            setPlatformFilter(v || undefined);
            setPage(1);
            setSelectedRowKeys([]);
          }}
          style={{ width: 100 }}
          options={[
            { label: '抖音', value: 'douyin' },
            { label: 'B站', value: 'bilibili' },
          ]}
        />
        <Button icon={<ReloadOutlined />} onClick={() => {
          handleBatchRetryFailed();
        }}>
          重试所有失败
        </Button>

        {selectedRowKeys.length > 0 && (
          <Popconfirm
            title={`确认删除选中的 ${selectedRowKeys.length} 个任务?`}
            onConfirm={handleBatchDelete}
          >
            <Button danger icon={<DeleteOutlined />}>
              批量删除 {selectedRowKeys.length} 条
            </Button>
          </Popconfirm>
        )}

        <Dropdown
          menu={{
            items: [
              { key: 'completed', label: '清理已完成', onClick: () => handleCleanup('completed') },
              { key: 'failed', label: '清理失败', onClick: () => handleCleanup('failed') },
              { key: 'all', label: '清理已完成+失败', onClick: () => handleCleanup('all') },
            ],
          }}
        >
          <Button icon={<ClearOutlined />}>清理</Button>
        </Dropdown>

        {ytdlpInfo?.available && (
          <Tag color="success" icon={<CheckCircleOutlined />}>
            yt-dlp v{ytdlpInfo.version}
          </Tag>
        )}
      </Space>

      {/* 任务表格 */}
      <Table
        dataSource={data?.items || []}
        columns={columns}
        rowKey="id"
        loading={isLoading}
        size="small"
        rowSelection={{
          selectedRowKeys,
          onChange: (keys) => setSelectedRowKeys(keys as number[]),
          getCheckboxProps: (record: DownloadTask) => ({
            disabled: record.status === 'downloading',
          }),
        }}
        pagination={data?.total ? {
          current: page,
          total: data.total,
          pageSize: 20,
          onChange: (p) => {
            setPage(p);
            setSelectedRowKeys([]);
          },
          showTotal: (t) => `共 ${t} 条`,
        } : false}
        locale={{ emptyText: '暂无下载任务' }}
      />

      {/* 视频预览 */}
      <Modal
        open={!!previewTask}
        title={previewTask?.title || '视频预览'}
        footer={null}
        onCancel={() => setPreviewTask(null)}
        width={720}
        destroyOnClose
      >
        {previewTask && (
          <>
            <video
              controls
              autoPlay
              src={fileUrl(previewTask.id)}
              style={{ width: '100%', maxHeight: '60vh', background: '#000' }}
            />
            <div style={{ marginTop: 8, color: '#888', fontSize: 12, wordBreak: 'break-all' }}>
              文件位置：{previewTask.file_path || '未知'}
            </div>
          </>
        )}
      </Modal>
    </div>
  );
}
