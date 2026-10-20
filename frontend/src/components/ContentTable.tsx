import { Table, Tag, Image, Button, Tooltip, Space } from 'antd';
import {
  DownloadOutlined, CheckOutlined, ExperimentOutlined,
  EyeOutlined, ReloadOutlined, LoadingOutlined,
} from '@ant-design/icons';
import type { Content } from '../api/queries';
import type { TableRowSelection } from 'antd/es/table/interface';
import dayjs from 'dayjs';

// 下载/分析状态的中文展示配置。分析状态为后端原始态，analyzing 为前端聚合态。
const DL_STATUS_CFG: Record<string, { color: string; text: string; icon?: React.ReactNode }> = {
  completed: { color: 'success', text: '已下载', icon: <CheckOutlined /> },
  downloading: { color: 'processing', text: '下载中', icon: <LoadingOutlined /> },
  pending: { color: 'default', text: '排队中' },
  failed: { color: 'error', text: '下载失败' },
};

const AN_STATUS_CFG: Record<string, { color: string; text: string; icon?: React.ReactNode }> = {
  completed: { color: 'success', text: '已分析', icon: <CheckOutlined /> },
  analyzing: { color: 'processing', text: '分析中', icon: <LoadingOutlined /> },
  pending: { color: 'default', text: '排队中' },
  extracting: { color: 'processing', text: '抽音频', icon: <LoadingOutlined /> },
  transcribing: { color: 'processing', text: '识别中', icon: <LoadingOutlined /> },
  failed: { color: 'error', text: '分析失败' },
};

const ANALYZING_STATES = ['pending', 'extracting', 'transcribing', 'analyzing'];

export interface ContentTableProps {
  data: Content[];
  loading?: boolean;
  total?: number;
  page?: number;
  onPageChange?: (page: number) => void;
  onRowClick?: (record: Content) => void;
  rowSelection?: TableRowSelection<Content>;
  // 传启用「数据状态 + 操作」列（内容检索打通下载/分析）
  onDownload?: (record: Content) => void;
  onRetryDownload?: (record: Content) => void;
  onAnalyze?: (record: Content) => void;
  onViewAnalysis?: (record: Content) => void;
  downloadingId?: number | null;
  analyzingId?: number | null;
}

export default function ContentTable({
  data, loading, total, page, onPageChange, onRowClick, rowSelection,
  onDownload, onRetryDownload, onAnalyze, onViewAnalysis,
  downloadingId, analyzingId,
}: ContentTableProps) {
  const showActions = !!(onDownload || onAnalyze);

  const columns = [
    {
      title: '封面', dataIndex: 'cover_url', key: 'cover', width: 80,
      render: (url: string) => url ? <Image src={url} width={60} height={80} style={{ objectFit: 'cover', borderRadius: 4 }} /> : <div style={{ width: 60, height: 80, background: '#f0f0f0', borderRadius: 4 }} />,
    },
    { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true, render: (t: string) => t || '(无标题)' },
    {
      title: '类型', dataIndex: 'content_type', key: 'type', width: 60,
      render: (t: string) => <Tag>{t === 'video' ? '视频' : t === 'image' ? '图文' : t}</Tag>,
    },
    { title: '点赞', dataIndex: 'likes', key: 'likes', width: 80, render: (n: number) => n?.toLocaleString() },
    { title: '评论', dataIndex: 'comments', key: 'comments', width: 80, render: (n: number) => n?.toLocaleString() },
    { title: '分享', dataIndex: 'shares', key: 'shares', width: 80, render: (n: number) => n?.toLocaleString() },
    { title: '播放', dataIndex: 'plays', key: 'plays', width: 80, render: (n: number) => n?.toLocaleString() },
    {
      title: '时间', dataIndex: 'create_time', key: 'create_time', width: 110,
      render: (t: string) => t ? dayjs(t).format('YYYY-MM-DD') : '-',
    },
    ...(showActions ? [
      {
        title: '数据状态', key: 'reuse_status', width: 120,
        render: (_: any, r: Content) => {
          const dl = r.download_status ? DL_STATUS_CFG[r.download_status] : null;
          const anAgg = r.analysis_status
            ? (ANALYZING_STATES.includes(r.analysis_status) ? 'analyzing' : r.analysis_status)
            : null;
          const an = anAgg ? AN_STATUS_CFG[anAgg] : null;
          return (
            <Space size={4} wrap>
              {dl
                ? <Tag icon={dl.icon} color={dl.color} style={{ marginInlineEnd: 0 }}>{dl.text}</Tag>
                : <Tag style={{ marginInlineEnd: 0 }}>未下载</Tag>}
              {an && <Tag icon={an.icon} color={an.color} style={{ marginInlineEnd: 0 }}>{an.text}</Tag>}
            </Space>
          );
        },
      },
      {
        title: '操作', key: 'actions', width: 150,
        render: (_: any, r: Content) => {
          const dl = r.download_status;
          const anAgg = r.analysis_status
            ? (ANALYZING_STATES.includes(r.analysis_status) ? 'analyzing' : r.analysis_status)
            : null;
          return (
            <Space size={4}>
              {dl === 'completed' ? (
                <Tooltip title="已有本地文件，分析时直接复用"><Button size="small" icon={<CheckOutlined />} disabled>已下载</Button></Tooltip>
              ) : dl === 'downloading' || dl === 'pending' ? (
                <Button size="small" disabled icon={<LoadingOutlined />}>下载中</Button>
              ) : dl === 'failed' ? (
                <Tooltip title="重试下载">
                  <Button size="small" icon={<ReloadOutlined />}
                    loading={downloadingId === r.id}
                    onClick={() => onRetryDownload?.(r)}>重试</Button>
                </Tooltip>
              ) : (
                <Tooltip title={r.content_type !== 'video' ? '仅视频可下载' : '下载到本地'}>
                  <Button size="small" icon={<DownloadOutlined />}
                    loading={downloadingId === r.id}
                    disabled={r.content_type !== 'video'}
                    onClick={() => onDownload?.(r)}>下载</Button>
                </Tooltip>
              )}
              {anAgg === 'completed' ? (
                <Button size="small" icon={<EyeOutlined />} onClick={() => onViewAnalysis?.(r)}>看分析</Button>
              ) : anAgg ? (
                <Button size="small" disabled icon={<LoadingOutlined />}>分析中</Button>
              ) : dl === 'completed' ? (
                <Tooltip title="基于已下载文件直接分析">
                  <Button size="small" type="primary" ghost icon={<ExperimentOutlined />}
                    loading={analyzingId === r.id}
                    onClick={() => onAnalyze?.(r)}>分析</Button>
                </Tooltip>
              ) : (
                <Tooltip title="需先下载才能分析">
                  <Button size="small" icon={<ExperimentOutlined />} disabled>分析</Button>
                </Tooltip>
              )}
            </Space>
          );
        },
      },
    ] : []),
  ];

  return (
    <Table
      dataSource={data}
      columns={columns}
      rowKey="id"
      loading={loading}
      size="small"
      rowSelection={rowSelection}
      pagination={total ? { current: page, total, onChange: onPageChange, pageSize: 20 } : false}
      onRow={(record) => ({
        onClick: () => onRowClick?.(record),
        style: { cursor: onRowClick ? 'pointer' : undefined },
      })}
    />
  );
}
