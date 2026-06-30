import { Table, Tag, Image } from 'antd';
import type { Content } from '../api/queries';
import type { TableRowSelection } from 'antd/es/table/interface';
import dayjs from 'dayjs';

interface ContentTableProps {
  data: Content[];
  loading?: boolean;
  total?: number;
  page?: number;
  onPageChange?: (page: number) => void;
  onRowClick?: (record: Content) => void;
  rowSelection?: TableRowSelection<Content>;
}

export default function ContentTable({ data, loading, total, page, onPageChange, onRowClick, rowSelection }: ContentTableProps) {
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
      title: '时间', dataIndex: 'create_time', key: 'create_time', width: 120,
      render: (t: string) => t ? dayjs(t).format('YYYY-MM-DD') : '-',
    },
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
